import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or None
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
# Must match vector(...) in db/schema.sql. pgvector's HNSW index supports up to 2000 dims.
EMBED_DIM = 768

# Tiger Data (Postgres + pgvector). Empty means use the local in-memory index.
DATABASE_URL = os.getenv("DATABASE_URL") or None
SCHEMA_PATH = BACKEND_DIR / "db" / "schema.sql"

DATA_DIR = BACKEND_DIR / "data"
LAW_TEXT_DIR = DATA_DIR / "law"
LOCAL_INDEX_PATH = DATA_DIR / "law_index.npz"
REVIEWS_DB_PATH = DATA_DIR / "reviews.sqlite3"

FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"
STANDALONE_FRONTEND = BACKEND_DIR.parent / "index.html"
