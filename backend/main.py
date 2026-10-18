import json
import logging
import secrets

import psycopg
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google.genai.errors import APIError
from pypdf.errors import PdfReadError
from starlette.middleware.sessions import SessionMiddleware

from app import auth, cases, config, pipeline
from app.pipeline import AnalysisInputError

app = FastAPI(title="Tenancy Demystifier")
logger = logging.getLogger(__name__)
MAX_AGREEMENT_SIZE = 10 * 1024 * 1024

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


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "gemini_configured": bool(config.GEMINI_API_KEY),
        "vector_store": "tiger_data" if config.DATABASE_URL else "local",
        "case_store": "tiger_data" if config.DATABASE_URL else "none",
    }


@app.post("/api/analyze")
async def analyze(
    request: Request,
    agreement: UploadFile = File(...),
    lead_tenant: str | None = Form(default=None),
    co_tenants: str = Form(default=""),
    property_address: str = Form(...),
    tenant_name: str | None = Form(default=None),
):
    lead_tenant = (lead_tenant or tenant_name or "").strip()
    if not lead_tenant:
        raise HTTPException(status_code=422, detail="Please enter the lead tenant's name")
    co_tenant_names = [name.strip() for name in co_tenants.split(",") if name.strip()]

    if agreement.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Please upload a PDF")

    try:
        pdf_bytes = await agreement.read(MAX_AGREEMENT_SIZE + 1)
        if len(pdf_bytes) > MAX_AGREEMENT_SIZE:
            raise HTTPException(status_code=413, detail="The PDF must be 10MB or smaller")
        if not pdf_bytes:
            raise HTTPException(status_code=400, detail="The PDF is empty")
        result = pipeline.analyze(pdf_bytes, lead_tenant, property_address, co_tenant_names)
    except PdfReadError:
        raise HTTPException(status_code=400, detail="Could not read that PDF")
    except AnalysisInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except APIError as error:
        logger.exception("Gemini API request failed during agreement analysis")
        if error.code == 429 or error.code >= 500:
            raise HTTPException(
                status_code=503, detail="Gemini is busy right now. Please try again in a minute."
            ) from error
        raise HTTPException(
            status_code=502,
            detail="Gemini could not analyze this agreement. Check the backend terminal for the provider error, then try again.",
        ) from error
    except (RuntimeError, json.JSONDecodeError) as error:
        logger.exception("Gemini returned an unusable analysis")
        raise HTTPException(
            status_code=502, detail="Gemini returned an unexpected answer. Please try again."
        ) from error

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
