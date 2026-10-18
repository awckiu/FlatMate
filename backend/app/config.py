import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or None
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
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
PHOTO_STORAGE_DIR = DATA_DIR / "photos"
PHOTO_STORAGE_ENDPOINT = os.getenv("PHOTO_STORAGE_ENDPOINT") or None
PHOTO_STORAGE_BUCKET = os.getenv("PHOTO_STORAGE_BUCKET") or None
PHOTO_STORAGE_ACCESS_KEY = os.getenv("PHOTO_STORAGE_ACCESS_KEY") or None
PHOTO_STORAGE_SECRET_KEY = os.getenv("PHOTO_STORAGE_SECRET_KEY") or None
PHOTO_STORAGE_REGION = os.getenv("PHOTO_STORAGE_REGION", "us-east-1")
SESSION_COOKIE_NAME = "flatmate_session"
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
REMINDER_SMTP_HOST = os.getenv("REMINDER_SMTP_HOST") or None
REMINDER_SMTP_PORT = int(os.getenv("REMINDER_SMTP_PORT", "587"))
REMINDER_SMTP_USERNAME = os.getenv("REMINDER_SMTP_USERNAME") or None
REMINDER_SMTP_PASSWORD = os.getenv("REMINDER_SMTP_PASSWORD") or None
REMINDER_SMTP_USE_SSL = os.getenv("REMINDER_SMTP_USE_SSL", "false").lower() == "true"
REMINDER_FROM_EMAIL = os.getenv("REMINDER_FROM_EMAIL") or REMINDER_SMTP_USERNAME

EMAIL_REMINDERS_CONFIGURED = bool(
    REMINDER_SMTP_HOST
    and REMINDER_FROM_EMAIL
    and REMINDER_SMTP_PORT > 0
    and (
        (REMINDER_SMTP_USERNAME is None and REMINDER_SMTP_PASSWORD is None)
        or (REMINDER_SMTP_USERNAME and REMINDER_SMTP_PASSWORD)
    )
)

FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"
STANDALONE_FRONTEND = BACKEND_DIR.parent / "index.html"
