from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pypdf.errors import PdfReadError

from app import config, pipeline

app = FastAPI(title="Tenancy Demystifier")


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
    tenant_name: str = Form(...),
    property_address: str = Form(...),
):
    if agreement.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Please upload a PDF")

    try:
        result = pipeline.analyze(await agreement.read(), tenant_name, property_address)
    except PdfReadError:
        raise HTTPException(status_code=400, detail="Could not read that PDF")

    return {
        "tenant_name": tenant_name,
        "property_address": property_address,
        "filename": agreement.filename,
        **result,
    }


# In production, serve the built React app from the same process and URL.
if config.FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        return FileResponse(config.FRONTEND_DIST / "index.html")
