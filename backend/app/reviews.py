"""Persist completed agreement reviews in PostgreSQL or a local SQLite database."""

import json
import secrets
import sqlite3
from contextlib import closing
from typing import Any

import psycopg

from app import config

CODE_PREFIX = "FLAT-"
# No 0/O/1/I/L so case codes are easy to read out and type.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6


def new_code() -> str:
    return CODE_PREFIX + "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def normalize_code(code: str) -> str:
    code = code.strip().upper()
    return code if code.startswith(CODE_PREFIX) else CODE_PREFIX + code


def save_review(
    lead_tenant: str,
    co_tenants: list[str],
    property_address: str,
    filename: str | None,
    review: dict[str, Any],
) -> str:
    """Save a review and return its case code, which is how it is reopened and shared."""
    save = _save_postgres if config.DATABASE_URL else _save_sqlite
    for _ in range(5):
        review_id = new_code()
        try:
            save(review_id, lead_tenant, co_tenants, property_address, filename, review)
            return review_id
        except (psycopg.errors.UniqueViolation, sqlite3.IntegrityError):
            continue
    raise RuntimeError("Could not generate a unique case code")


def get_review(review_id: str) -> dict[str, Any] | None:
    """A saved review in the same shape as the /api/analyze response, or None."""
    query = (
        "SELECT lead_tenant, co_tenants, property_address, filename, review "
        "FROM tenancy_reviews WHERE id = {}"
    )
    try:
        if config.DATABASE_URL:
            with psycopg.connect(config.DATABASE_URL) as connection:
                row = connection.execute(query.format("%s"), (review_id,)).fetchone()
        else:
            if not config.REVIEWS_DB_PATH.exists():
                return None
            with closing(sqlite3.connect(config.REVIEWS_DB_PATH, timeout=10)) as connection:
                row = connection.execute(query.format("?"), (review_id,)).fetchone()
    # The table is created on the first save, so it may not exist yet.
    except psycopg.errors.UndefinedTable:
        return None
    except sqlite3.OperationalError as error:
        if "no such table" not in str(error):
            raise
        return None
    if row is None:
        return None

    lead_tenant, co_tenants, property_address, filename, review = row
    # Postgres returns jsonb as Python objects; SQLite stores JSON text.
    co_tenants = json.loads(co_tenants) if isinstance(co_tenants, str) else co_tenants
    review = json.loads(review) if isinstance(review, str) else review
    return {
        "review_id": review_id,
        "tenant_name": lead_tenant,
        "lead_tenant": lead_tenant,
        "co_tenants": co_tenants,
        "tenants": [
            {"name": lead_tenant, "role": "lead"},
            *({"name": name, "role": "co_tenant"} for name in co_tenants),
        ],
        "property_address": property_address,
        "filename": filename,
        **review,
    }


def _save_postgres(
    review_id: str,
    lead_tenant: str,
    co_tenants: list[str],
    property_address: str,
    filename: str | None,
    review: dict[str, Any],
) -> None:
    with psycopg.connect(config.DATABASE_URL) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tenancy_reviews (
                id text PRIMARY KEY,
                lead_tenant text NOT NULL,
                co_tenants jsonb NOT NULL,
                property_address text NOT NULL,
                filename text,
                review jsonb NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        connection.execute(
            """
            INSERT INTO tenancy_reviews
                (id, lead_tenant, co_tenants, property_address, filename, review)
            VALUES (%s, %s, %s::jsonb, %s, %s, %s::jsonb)
            """,
            (
                review_id,
                lead_tenant,
                json.dumps(co_tenants),
                property_address,
                filename,
                json.dumps(review),
            ),
        )


def _save_sqlite(
    review_id: str,
    lead_tenant: str,
    co_tenants: list[str],
    property_address: str,
    filename: str | None,
    review: dict[str, Any],
) -> None:
    config.REVIEWS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(config.REVIEWS_DB_PATH, timeout=10)) as connection:
        with connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tenancy_reviews (
                    id text PRIMARY KEY,
                    lead_tenant text NOT NULL,
                    co_tenants text NOT NULL,
                    property_address text NOT NULL,
                    filename text,
                    review text NOT NULL,
                    created_at text NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                INSERT INTO tenancy_reviews
                    (id, lead_tenant, co_tenants, property_address, filename, review)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    lead_tenant,
                    json.dumps(co_tenants),
                    property_address,
                    filename,
                    json.dumps(review),
                ),
            )
