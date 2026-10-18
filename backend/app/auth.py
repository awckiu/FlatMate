"""Email + password accounts. The signed session cookie holds only the user's id."""

import hashlib
import hmac
import logging
import secrets

import psycopg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app import cases, config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

MIN_PASSWORD_LENGTH = 8
# scrypt from the standard library, at one of OWASP's minimum settings. This one needs
# 16 MB per hash rather than 128 MB, which suits a small VM.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 5
LOGIN_FAILED = "That email and password don't match an account."


class SignupRequest(BaseModel):
    email: str
    password: str
    display_name: str


class LoginRequest(BaseModel):
    email: str
    password: str


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    candidate = _scrypt(password, bytes.fromhex(salt), int(n), int(r), int(p))
    return hmac.compare_digest(candidate, bytes.fromhex(digest))


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def current_user_id(request: Request) -> str | None:
    return request.session.get("user_id")


def require_user_id(request: Request) -> str:
    user_id = current_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Please log in first.")
    return user_id


def require_database():
    if not config.DATABASE_URL:
        raise HTTPException(status_code=503, detail="Accounts need the database, which isn't set up on this server.")


def _user_json(row) -> dict:
    user_id, email, display_name = row
    return {"id": str(user_id), "email": email, "display_name": display_name}


@router.post("/auth/signup")
def signup(body: SignupRequest, request: Request):
    require_database()
    email = normalize_email(body.email)
    display_name = body.display_name.strip()
    if "@" not in email or not display_name:
        raise HTTPException(status_code=422, detail="Please enter your name and a valid email address.")
    if len(body.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=422, detail=f"Use a password of at least {MIN_PASSWORD_LENGTH} characters.")
    try:
        with cases.connect() as conn:
            row = conn.execute(
                """INSERT INTO users (email, display_name, password_hash) VALUES (%s, %s, %s)
                   ON CONFLICT (email) DO NOTHING RETURNING id, email, display_name""",
                (email, display_name, hash_password(body.password)),
            ).fetchone()
    except psycopg.Error as error:
        logger.exception("Could not create an account")
        raise HTTPException(status_code=503, detail="Could not reach the database. Please try again.") from error
    if row is None:
        raise HTTPException(status_code=409, detail="An account with that email already exists. Try logging in.")
    request.session["user_id"] = str(row[0])
    return _user_json(row)


@router.post("/auth/login")
def login(body: LoginRequest, request: Request):
    require_database()
    try:
        with cases.connect() as conn:
            row = conn.execute(
                "SELECT id, email, display_name, password_hash FROM users WHERE email = %s",
                (normalize_email(body.email),),
            ).fetchone()
    except psycopg.Error as error:
        logger.exception("Could not look up an account")
        raise HTTPException(status_code=503, detail="Could not reach the database. Please try again.") from error
    if row is None or not row[3] or not verify_password(body.password, row[3]):
        raise HTTPException(status_code=401, detail=LOGIN_FAILED)
    request.session["user_id"] = str(row[0])
    return _user_json(row[:3])


@router.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    user_id = require_user_id(request)
    require_database()
    try:
        with cases.connect() as conn:
            row = conn.execute("SELECT id, email, display_name FROM users WHERE id = %s", (user_id,)).fetchone()
    except psycopg.Error as error:
        logger.exception("Could not load the logged-in user")
        raise HTTPException(status_code=503, detail="Could not reach the database. Please try again.") from error
    if row is None:
        # The account was deleted since the cookie was set.
        request.session.clear()
        raise HTTPException(status_code=401, detail="Please log in first.")
    return _user_json(row)
