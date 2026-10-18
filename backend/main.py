import json
import logging

import psycopg
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google.genai import errors as gemini_errors
from pypdf.errors import PdfReadError

from app import cases, config, pipeline
from app.pipeline import AnalysisInputError

logger = logging.getLogger(__name__)
app = FastAPI(title="Tenancy Demystifier")
MAX_AGREEMENT_SIZE = 10 * 1024 * 1024


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "gemini_configured": bool(config.GEMINI_API_KEY),
        "vector_store": "tiger_data" if config.DATABASE_URL else "local",
    }


@app.post("/api/analyze")
async def analyze(
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
    except gemini_errors.APIError as error:
        logger.warning("Gemini API error %s: %s", error.code, error)
        if error.code == 429 or error.code >= 500:
            raise HTTPException(
                status_code=503, detail="Gemini is busy right now. Please try again in a minute."
            ) from error
        raise HTTPException(
            status_code=502, detail="The analysis service rejected the request. Please try again later."
        ) from error
    except (RuntimeError, json.JSONDecodeError) as error:
        logger.warning("Gemini returned an unusable analysis: %s", error)
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
