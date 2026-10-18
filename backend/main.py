import asyncio
import json
import logging
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import date
from typing import Literal

import psycopg
from fastapi import Cookie, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from google.genai.errors import APIError
from pypdf.errors import PdfReadError

from pydantic import BaseModel, Field

from app import bills, calendar, config, llm, media, pipeline, reviews, tenancies
from app.pipeline import AnalysisInputError
from app.rag.retrieve import retrieve_many

logger = logging.getLogger(__name__)
MAX_AGREEMENT_SIZE = 10 * 1024 * 1024
SESSION_MAX_AGE = tenancies.SESSION_DAYS * 24 * 60 * 60


async def _calendar_reminder_worker() -> None:
    while True:
        try:
            if config.EMAIL_REMINDERS_CONFIGURED:
                await asyncio.to_thread(calendar.send_due_reminders)
        except Exception:
            logger.exception("Calendar email reminder check failed")
        await asyncio.sleep(60 * 60)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(_calendar_reminder_worker())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Tenancy Demystifier", lifespan=lifespan)


def optional_account(
    session: str | None = Cookie(default=None, alias=config.SESSION_COOKIE_NAME),
) -> dict[str, str] | None:
    return tenancies.account_for_session(session)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "gemini_configured": bool(config.GEMINI_API_KEY),
        "vector_store": "tiger_data" if config.DATABASE_URL else "local",
        "review_store": "postgres" if config.DATABASE_URL else "sqlite",
        "email_reminders_configured": config.EMAIL_REMINDERS_CONFIGURED,
    }


@app.post("/api/analyze")
async def analyze(
    agreement: UploadFile = File(...),
    lead_tenant: str | None = Form(default=None),
    co_tenants: str = Form(default=""),
    property_address: str = Form(...),
    tenant_name: str | None = Form(default=None),
    household_mode: str = Form(default="single"),
    hmo_status: str = Form(default="unknown"),
    account: dict[str, str] | None = Depends(optional_account),
):
    lead_tenant = (lead_tenant or tenant_name or "").strip()
    if not lead_tenant:
        raise HTTPException(status_code=422, detail="Please enter the lead tenant's name")
    if household_mode not in tenancies.HOUSEHOLD_MODES:
        raise HTTPException(status_code=422, detail="Please choose a household setup")
    if hmo_status not in {"yes", "no", "unknown", "not_applicable"}:
        raise HTTPException(status_code=422, detail="Please choose an HMO status")
    if household_mode == "friends" and hmo_status == "not_applicable":
        raise HTTPException(status_code=422, detail="Choose an HMO status when living with friends")
    co_tenant_names = [name.strip() for name in co_tenants.split(",") if name.strip()]

    if agreement.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Please upload a PDF")

    try:
        pdf_bytes = await agreement.read(MAX_AGREEMENT_SIZE + 1)
        if len(pdf_bytes) > MAX_AGREEMENT_SIZE:
            raise HTTPException(status_code=413, detail="The PDF must be 10MB or smaller")
        if not pdf_bytes:
            raise HTTPException(status_code=400, detail="The PDF is empty")
        result = pipeline.analyze(
            pdf_bytes,
            lead_tenant,
            property_address,
            co_tenant_names,
            household_mode,
            hmo_status,
        )
        result["household_mode"] = household_mode
        result["hmo_status"] = hmo_status
    except PdfReadError:
        raise HTTPException(status_code=400, detail="Could not read that PDF")
    except AnalysisInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except APIError as error:
        logger.exception("Gemini API request failed during agreement analysis")
        status_code = error.code if isinstance(error.code, int) else None
        if status_code == 429 or (status_code is not None and status_code >= 500):
            raise HTTPException(
                status_code=503,
                detail="Gemini is busy right now. Please try again in a minute.",
            ) from error
        raise HTTPException(
            status_code=502,
            detail="Gemini could not analyze this agreement. Check the backend terminal for the provider error, then try again.",
        ) from error
    except (RuntimeError, json.JSONDecodeError) as error:
        logger.exception("Gemini returned an unusable agreement analysis")
        raise HTTPException(
            status_code=502,
            detail="Gemini returned an unexpected answer. Please try again.",
        ) from error
    bills_estimate = bills.estimate_bills(
        property_address=property_address,
        tenant_count=len(co_tenant_names) + 1,
        need_to_knows=result.get("need_to_knows"),
        lead_tenant=lead_tenant,
        co_tenants=co_tenant_names,
    )
    result["bills_estimate"] = bills_estimate
    try:
        review_id = reviews.save_review(
            lead_tenant,
            co_tenant_names,
            property_address,
            agreement.filename,
            result,
            owner_account_id=account["id"] if account else None,
        )
    except (OSError, sqlite3.Error, psycopg.Error) as error:
        logger.exception("Could not save completed agreement review")
        raise HTTPException(
            status_code=503,
            detail="The agreement was analyzed, but its review could not be saved. Please try again.",
        ) from error

    return {
        "review_id": review_id,
        "case_code": review_id,
        "tenant_name": lead_tenant,
        "lead_tenant": lead_tenant,
        "co_tenants": co_tenant_names,
        "tenants": [
            {"name": lead_tenant, "role": "lead"},
            *({"name": name, "role": "co_tenant"} for name in co_tenant_names),
        ],
        "property_address": property_address,
        "filename": agreement.filename,
        **result,
    }


@app.get("/api/reviews/{review_code}")
def get_shared_review(review_code: str):
    try:
        review = reviews.get_shared_review(review_code)
    except (OSError, sqlite3.Error, psycopg.Error) as error:
        logger.exception("Could not load shared review %s", review_code)
        raise HTTPException(
            status_code=503,
            detail="Could not load the saved review right now. Please try again.",
        ) from error
    if review is None:
        raise HTTPException(status_code=404, detail="No saved review was found with that code.")
    return review


class BillsEstimateRequest(BaseModel):
    property_address: str = Field(min_length=1, max_length=500)
    tenant_count: int = Field(default=1, ge=1, le=10)
    is_student: bool = Field(
        default=False,
        description="User confirms all household members meet the applicable full-time student criteria.",
    )
    bill_selections: dict[str, bool] = Field(default_factory=dict)
    included_overrides: dict[str, bool] = Field(default_factory=dict)
    lead_tenant: str = "Lead Tenant"
    co_tenants: list[str] = Field(default_factory=list)


@app.post("/api/bills/estimate")
def estimate_bills_endpoint(req: BillsEstimateRequest):
    return bills.estimate_bills(
        property_address=req.property_address,
        tenant_count=req.tenant_count,
        is_student=req.is_student,
        bill_selections=req.bill_selections,
        included_overrides=req.included_overrides,
        lead_tenant=req.lead_tenant,
        co_tenants=req.co_tenants,
    )


class AccountRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=256)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class JoinTenancyRequest(BaseModel):
    code: str = Field(min_length=8, max_length=20)
    tenant_name: str = Field(min_length=1, max_length=100)
    role: str


class TenancyProfileRequest(BaseModel):
    household_mode: str
    hmo_status: str
    property_address: str = Field(min_length=1, max_length=500)
    lead_tenant: str = Field(min_length=1, max_length=100)
    co_tenants: list[str] = Field(default_factory=list, max_length=10)
    current_tenant: str = Field(min_length=1, max_length=100)
    current_role: str
    agreement_signed: bool = False
    signed_on: str | None = None
    tenancy_start: str | None = None
    review_id: str = Field(min_length=1, max_length=100)


class SigningUpdateRequest(BaseModel):
    agreement_signed: bool
    signed_on: str | None = None
    tenancy_start: str | None = None


class TenancyChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    request_landlord_draft: bool = False
    contact_preference: str = "email"


class CalendarItemRequest(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    kind: Literal["rent", "bill"]
    due_date: date
    recurrence: Literal["once", "monthly"]
    amount: float | None = Field(default=None, ge=0, le=1_000_000)


class CalendarReminderPreferenceRequest(BaseModel):
    enabled: bool


def current_account(
    account: dict[str, str] | None = Depends(optional_account),
) -> dict[str, str]:
    if account is None:
        raise HTTPException(status_code=401, detail="Sign in to access your tenancy profile.")
    return account


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        config.SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=config.SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def _profile_or_404(profile: dict | None) -> dict:
    if profile is None:
        raise HTTPException(status_code=404, detail="Tenancy profile not found.")
    return profile


@app.post("/api/auth/register", status_code=201)
def register_account(body: AccountRequest, response: Response):
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", body.email.strip()):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    try:
        account, token = tenancies.register(body.email, body.display_name, body.password)
    except (sqlite3.IntegrityError, psycopg.errors.UniqueViolation) as error:
        raise HTTPException(status_code=409, detail="An account with that email already exists.") from error
    _set_session_cookie(response, token)
    return account


@app.post("/api/auth/login")
def login_account(body: LoginRequest, response: Response):
    account_session = tenancies.login(body.email, body.password)
    if account_session is None:
        raise HTTPException(status_code=401, detail="Email or password was not recognized.")
    account, token = account_session
    _set_session_cookie(response, token)
    return account


@app.get("/api/auth/me")
def get_current_account(account: dict[str, str] = Depends(current_account)):
    return account


@app.post("/api/auth/logout", status_code=204)
def logout_account(
    response: Response,
    session: str | None = Cookie(default=None, alias=config.SESSION_COOKIE_NAME),
    _account: dict[str, str] = Depends(current_account),
):
    tenancies.logout(session)
    response.delete_cookie(
        config.SESSION_COOKIE_NAME,
        httponly=True,
        secure=config.SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


@app.get("/api/tenancies")
def list_tenancies(account: dict[str, str] = Depends(current_account)):
    return tenancies.list_profiles(account["id"])


@app.post("/api/tenancies", status_code=201)
def create_tenancy_profile(
    body: TenancyProfileRequest,
    account: dict[str, str] = Depends(current_account),
):
    review = reviews.get_review(body.review_id, account["id"])
    if review is None:
        raise HTTPException(status_code=404, detail="The agreement review could not be found.")
    try:
        return tenancies.create_profile(
            account,
            body.household_mode,
            body.hmo_status,
            body.property_address,
            body.lead_tenant,
            body.co_tenants,
            body.current_tenant,
            body.current_role,
            body.review_id,
            review,
            body.agreement_signed,
            body.signed_on,
            body.tenancy_start,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/tenancies/join")
def join_tenancy(
    body: JoinTenancyRequest,
    account: dict[str, str] = Depends(current_account),
):
    try:
        profile = tenancies.join_profile(body.code, account, body.tenant_name, body.role)
    except (ValueError, sqlite3.IntegrityError, psycopg.errors.UniqueViolation) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _profile_or_404(profile)


@app.get("/api/tenancies/{profile_id}")
def get_tenancy_profile(
    profile_id: str,
    account: dict[str, str] = Depends(current_account),
):
    profile = tenancies.get_profile(profile_id, account["id"])
    return _profile_or_404(profile)


@app.patch("/api/tenancies/{profile_id}/signing")
def update_tenancy_signing(
    profile_id: str,
    body: SigningUpdateRequest,
    account: dict[str, str] = Depends(current_account),
):
    try:
        profile = tenancies.update_signing(
            profile_id,
            account["id"],
            body.agreement_signed,
            body.signed_on,
            body.tenancy_start,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _profile_or_404(profile)


@app.get("/api/tenancies/{profile_id}/calendar")
def get_tenancy_calendar(
    profile_id: str,
    month: str,
    account: dict[str, str] = Depends(current_account),
):
    profile = _profile_or_404(tenancies.get_profile(profile_id, account["id"]))
    try:
        month_start = date.fromisoformat(f"{month}-01")
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Calendar month must use YYYY-MM format.") from error
    year = month_start.year + (month_start.month == 12)
    month_number = 1 if month_start.month == 12 else month_start.month + 1
    month_end = date(year, month_number, 1)
    return {
        "month": month,
        "events": calendar.list_items(profile_id, account["id"], month_start, month_end),
        "email": account["email"],
        "email_reminders_enabled": calendar.get_email_preference(account["id"]),
        "email_reminders_configured": config.EMAIL_REMINDERS_CONFIGURED,
        "property_address": profile["property_address"],
    }


@app.post("/api/tenancies/{profile_id}/calendar", status_code=201)
def create_tenancy_calendar_item(
    profile_id: str,
    body: CalendarItemRequest,
    account: dict[str, str] = Depends(current_account),
):
    _profile_or_404(tenancies.get_profile(profile_id, account["id"]))
    try:
        return calendar.add_item(
            profile_id,
            account["id"],
            body.title,
            body.kind,
            body.due_date,
            body.recurrence,
            body.amount,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except PermissionError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.delete("/api/tenancies/{profile_id}/calendar/{item_id}", status_code=204)
def delete_tenancy_calendar_item(
    profile_id: str,
    item_id: str,
    account: dict[str, str] = Depends(current_account),
):
    if not calendar.delete_item(profile_id, item_id, account["id"]):
        raise HTTPException(status_code=404, detail="Calendar item not found.")
    return Response(status_code=204)


@app.put("/api/tenancies/{profile_id}/calendar/preferences")
def update_calendar_reminder_preference(
    profile_id: str,
    body: CalendarReminderPreferenceRequest,
    account: dict[str, str] = Depends(current_account),
):
    _profile_or_404(tenancies.get_profile(profile_id, account["id"]))
    if body.enabled and not config.EMAIL_REMINDERS_CONFIGURED:
        raise HTTPException(
            status_code=503,
            detail="Email reminders are not configured on this server yet.",
        )
    calendar.set_email_preference(account["id"], body.enabled)
    return {
        "email_reminders_enabled": body.enabled,
        "email_reminders_configured": config.EMAIL_REMINDERS_CONFIGURED,
        "email": account["email"],
    }


@app.get("/api/tenancies/{profile_id}/chat")
def get_tenancy_chat(
    profile_id: str,
    account: dict[str, str] = Depends(current_account),
):
    profile = _profile_or_404(tenancies.get_profile(profile_id, account["id"]))
    if not profile["agreement_signed"]:
        raise HTTPException(status_code=409, detail="The tenancy buddy unlocks once the agreement is signed.")
    return tenancies.list_chat_messages(profile_id, account["id"])


@app.get("/api/tenancies/{profile_id}/clause-issues")
def get_tenancy_clause_issues(
    profile_id: str,
    account: dict[str, str] = Depends(current_account),
):
    profile = _profile_or_404(tenancies.get_profile(profile_id, account["id"]))
    if not profile["agreement_signed"]:
        raise HTTPException(status_code=409, detail="Clause status tracking unlocks once the agreement is signed.")
    try:
        return tenancies.list_clause_issues(profile_id, account["id"])
    except PermissionError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/api/tenancies/{profile_id}/chat")
def tenancy_chat(
    profile_id: str,
    body: TenancyChatRequest,
    account: dict[str, str] = Depends(current_account),
):
    profile = _profile_or_404(tenancies.get_profile(profile_id, account["id"]))
    if not profile["agreement_signed"]:
        raise HTTPException(status_code=409, detail="The tenancy buddy unlocks once the agreement is signed.")
    if body.contact_preference not in {"email", "text"}:
        raise HTTPException(status_code=422, detail="Choose email or text for the landlord draft.")
    sections = retrieve_many(
        [
            f"Household setup: {profile['household_mode']}; HMO status: "
            f"{profile['hmo_status']}; property: {profile['property_address']}. "
            f"Tenant question: {body.message}"
        ]
    )[0]
    try:
        clause_issues = tenancies.list_clause_issues(profile_id, account["id"])
        result = llm.tenancy_buddy_reply(
            body.message.strip(),
            profile,
            sections,
            body.contact_preference,
            body.request_landlord_draft,
            clause_issues,
        )
        memory_updates = tenancies.record_clause_status_updates(
            profile_id,
            account["id"],
            profile["review"],
            result["issue_updates"],
            body.message.strip(),
        )
        message_id = tenancies.add_chat_message(
            profile_id,
            account["id"],
            body.message.strip(),
            result["reply"],
            result["landlord_draft"],
        )
    except APIError as error:
        logger.exception("Gemini API request failed during tenancy-buddy chat")
        raise HTTPException(status_code=502, detail="The tenancy buddy could not answer just now.") from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except PermissionError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {
        "id": message_id,
        "reply": result["reply"],
        "landlord_draft": result["landlord_draft"],
        "memory_updates": memory_updates,
        "law_data_available": bool(sections),
        "disclaimer": "FlatMate prepares drafts only and does not send messages. This is not legal advice.",
    }


@app.get("/api/tenancies/{profile_id}/photos")
def get_tenancy_photos(
    profile_id: str,
    account: dict[str, str] = Depends(current_account),
):
    _profile_or_404(tenancies.get_profile(profile_id, account["id"]))
    return tenancies.list_photos(profile_id, account["id"])


@app.post("/api/tenancies/{profile_id}/photos", status_code=201)
async def upload_tenancy_photo(
    profile_id: str,
    photo: UploadFile = File(...),
    room: str = Form(..., min_length=1, max_length=80),
    note: str = Form(default="", max_length=500),
    account: dict[str, str] = Depends(current_account),
):
    profile = _profile_or_404(tenancies.get_profile(profile_id, account["id"]))
    if not profile["is_active"]:
        raise HTTPException(status_code=409, detail="Move-in photo uploads unlock when the tenancy starts.")
    content = await photo.read(media.MAX_PHOTO_SIZE + 1)
    try:
        storage_key, media_type = media.store_photo(content, photo.content_type or "")
        photo_id = tenancies.add_photo(
            profile_id, account["id"], room.strip(), note.strip(), storage_key, media_type
        )
    except media.InvalidPhoto as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except RuntimeError as error:
        logger.exception("Could not store tenancy photo")
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"id": photo_id, "room": room.strip(), "media_type": media_type}


@app.get("/api/tenancies/{profile_id}/photos/{photo_id}")
def read_tenancy_photo(
    profile_id: str,
    photo_id: str,
    account: dict[str, str] = Depends(current_account),
):
    _profile_or_404(tenancies.get_profile(profile_id, account["id"]))
    photo = tenancies.get_photo(profile_id, photo_id, account["id"])
    if photo is None:
        raise HTTPException(status_code=404, detail="Photo not found.")
    try:
        content = media.read_photo(photo["storage_key"])
    except (OSError, RuntimeError) as error:
        logger.exception("Could not read stored tenancy photo")
        raise HTTPException(status_code=503, detail="The photo could not be loaded.") from error
    return Response(content=content, media_type=photo["media_type"])


@app.delete("/api/tenancies/{profile_id}/photos/{photo_id}", status_code=204)
def delete_tenancy_photo(
    profile_id: str,
    photo_id: str,
    account: dict[str, str] = Depends(current_account),
):
    _profile_or_404(tenancies.get_profile(profile_id, account["id"]))
    photo = tenancies.get_photo(profile_id, photo_id, account["id"])
    if photo is None:
        raise HTTPException(status_code=404, detail="Photo not found.")
    try:
        media.delete_photo(photo["storage_key"])
        tenancies.delete_photo(profile_id, photo_id, account["id"])
    except (OSError, RuntimeError) as error:
        logger.exception("Could not delete tenancy photo")
        raise HTTPException(status_code=503, detail="The photo could not be deleted.") from error


# Serve the standalone app when it is present; otherwise use the built React app.
if config.STANDALONE_FRONTEND.is_file():
    @app.get("/")
    def standalone():
        return FileResponse(config.STANDALONE_FRONTEND)
elif config.FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        return FileResponse(config.FRONTEND_DIST / "index.html")
