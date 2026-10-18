import asyncio
import json
import logging
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import date
from typing import Literal

import psycopg
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from google.genai.errors import APIError
from pypdf.errors import PdfReadError
from starlette.middleware.sessions import SessionMiddleware

from pydantic import BaseModel, Field

from app import auth, bills, calendar, cases, config, llm, media, pipeline, tenancies
from app.pipeline import AnalysisInputError
from app.rag.retrieve import retrieve_many

logger = logging.getLogger(__name__)
MAX_AGREEMENT_SIZE = 10 * 1024 * 1024


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


if not config.SESSION_SECRET:
    logger.warning("SESSION_SECRET is not set; logins will not survive a restart")
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET or secrets.token_urlsafe(32),
    session_cookie="flatmate_session",
    max_age=30 * 24 * 60 * 60,
    same_site="lax",
    https_only=config.SESSION_HTTPS_ONLY,
)
app.include_router(auth.router)


def optional_account(request: Request) -> dict[str, str] | None:
    """The logged-in user (app.auth), mirrored into the tenancy tables, or None."""
    user_id = auth.current_user_id(request)
    if not user_id or not config.DATABASE_URL:
        return None
    try:
        with cases.connect() as conn:
            row = conn.execute("SELECT id, email, display_name FROM users WHERE id = %s", (user_id,)).fetchone()
    except psycopg.Error as error:
        logger.exception("Could not load the logged-in user")
        raise HTTPException(status_code=503, detail="Could not reach the database. Please try again.") from error
    if row is None:
        return None
    account = {"id": str(row[0]), "email": row[1], "display_name": row[2]}
    tenancies.sync_account(account)
    return account


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "gemini_configured": bool(config.GEMINI_API_KEY),
        "vector_store": "tiger_data" if config.DATABASE_URL else "local",
        "case_store": "tiger_data" if config.DATABASE_URL else "none",
        "email_reminders_configured": config.EMAIL_REMINDERS_CONFIGURED,
    }


@app.post("/api/analyze")
async def analyze(
    request: Request,
    agreement: UploadFile = File(...),
    lead_tenant: str | None = Form(default=None),
    co_tenants: str = Form(default=""),
    property_address: str = Form(...),
    tenant_name: str | None = Form(default=None),
    household_mode: str = Form(default="single"),
    hmo_status: str = Form(default="unknown"),
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
    agreement_text = result.pop("agreement_text", "")
    clauses = result.pop("clauses", [])
    case_code = None
    if config.DATABASE_URL:
        # Saving must never break the demo: show the results even if the database is down.
        try:
            with cases.connect() as conn:
                case_code = cases.save_case(
                    conn,
                    lead_tenant=lead_tenant,
                    co_tenants=co_tenant_names,
                    property_address=property_address,
                    filename=agreement.filename,
                    agreement_text=agreement_text,
                    clauses=clauses,
                    result=result,
                    user_id=auth.current_user_id(request),
                )
        except Exception:
            logger.exception("Could not save the case")

    return {
        "case_code": case_code,
        "saved": case_code is not None,
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
    case_code: str = Field(min_length=1, max_length=100)


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


def _profile_or_404(profile: dict | None) -> dict:
    if profile is None:
        raise HTTPException(status_code=404, detail="Tenancy profile not found.")
    return profile


@app.get("/api/tenancies")
def list_tenancies(account: dict[str, str] = Depends(current_account)):
    return tenancies.list_profiles(account["id"])


@app.post("/api/tenancies", status_code=201)
def create_tenancy_profile(
    body: TenancyProfileRequest,
    account: dict[str, str] = Depends(current_account),
):
    try:
        with cases.connect() as conn:
            review = cases.get_case(conn, cases.normalize_code(body.case_code))
    except psycopg.Error as error:
        logger.exception("Could not load case %s for a tenancy profile", body.case_code)
        raise HTTPException(status_code=503, detail="Could not reach the database. Please try again.") from error
    if review is None:
        raise HTTPException(status_code=404, detail="The agreement review could not be found.")
    review["bills_estimate"] = bills.estimate_bills(
        property_address=review["property_address"],
        tenant_count=len(review["co_tenants"]) + 1,
        need_to_knows=review.get("need_to_knows"),
        lead_tenant=review["lead_tenant"],
        co_tenants=review["co_tenants"],
    )
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
            review["case_code"],
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
        result = llm.tenancy_buddy_reply(
            body.message.strip(),
            profile,
            sections,
            body.contact_preference,
            body.request_landlord_draft,
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
    return {
        "id": message_id,
        "reply": result["reply"],
        "landlord_draft": result["landlord_draft"],
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


@app.get("/api/cases/{code}")
def get_case(code: str):
    if not config.DATABASE_URL:
        raise HTTPException(status_code=503, detail="Saved cases need the database, which isn't set up on this server.")
    try:
        with cases.connect() as conn:
            case = cases.get_case(conn, cases.normalize_code(code))
    except psycopg.Error as error:
        logger.exception("Could not load case %s", code)
        raise HTTPException(status_code=503, detail="Could not reach the database. Please try again.") from error
    if case is None:
        raise HTTPException(status_code=404, detail="No case was found with that code.")
    return case


@app.get("/api/my/cases")
def my_cases(request: Request):
    user_id = auth.require_user_id(request)
    auth.require_database()
    try:
        with cases.connect() as conn:
            return {"cases": cases.list_cases(conn, user_id)}
    except psycopg.Error as error:
        logger.exception("Could not list cases")
        raise HTTPException(status_code=503, detail="Could not reach the database. Please try again.") from error


@app.post("/api/cases/{code}/join")
def join_case(code: str, request: Request):
    """Add a case to the logged-in user's account, e.g. a co-tenant entering the code."""
    user = auth.me(request)
    try:
        with cases.connect() as conn:
            found = cases.join_case(conn, cases.normalize_code(code), user["id"], user["display_name"])
    except psycopg.Error as error:
        logger.exception("Could not join case %s", code)
        raise HTTPException(status_code=503, detail="Could not reach the database. Please try again.") from error
    if not found:
        raise HTTPException(status_code=404, detail="No case was found with that code.")
    return {"ok": True}


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
