from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pypdf.errors import PdfReadError

from app import config, pipeline
from app.pipeline import AnalysisInputError

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

    return {
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
