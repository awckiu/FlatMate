"""Persist completed agreement reviews in PostgreSQL or a local SQLite database."""

import json
import re
import secrets
import sqlite3
from contextlib import closing
from typing import Any

import psycopg

from app import config

REVIEW_CODE_PREFIX = "FLAT-"
REVIEW_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
REVIEW_CODE_LENGTH = 10


def save_review(
    lead_tenant: str,
    co_tenants: list[str],
    property_address: str,
    filename: str | None,
    review: dict[str, Any],
    owner_account_id: str | None = None,
) -> str:
    for _ in range(5):
        review_id = REVIEW_CODE_PREFIX + "".join(
            secrets.choice(REVIEW_CODE_ALPHABET) for _ in range(REVIEW_CODE_LENGTH)
        )
        try:
            if config.DATABASE_URL:
                _save_postgres(
                    review_id, lead_tenant, co_tenants, property_address, filename, review, owner_account_id
                )
            else:
                _save_sqlite(
                    review_id, lead_tenant, co_tenants, property_address, filename, review, owner_account_id
                )
            return review_id
        except (psycopg.errors.UniqueViolation, sqlite3.IntegrityError):
            continue
    raise RuntimeError("Could not generate a unique review code")


def get_review(review_id: str, owner_account_id: str) -> dict[str, Any] | None:
    """Load a saved agreement review for its owner's tenancy profile."""
    if config.DATABASE_URL:
        with psycopg.connect(config.DATABASE_URL) as connection:
            row = connection.execute(
                """
                SELECT review FROM tenancy_reviews
                WHERE id = %s AND owner_account_id = %s
                """,
                (review_id, owner_account_id),
            ).fetchone()
    else:
        if not config.REVIEWS_DB_PATH.exists():
            return None
        with closing(sqlite3.connect(config.REVIEWS_DB_PATH, timeout=10)) as connection:
            row = connection.execute(
                """
                SELECT review FROM tenancy_reviews
                WHERE id = ? AND owner_account_id = ?
                """,
                (review_id, owner_account_id),
            ).fetchone()
    if row is None:
        return None
    return row[0] if isinstance(row[0], dict) else json.loads(row[0])


def get_shared_review(review_code: str) -> dict[str, Any] | None:
    """Load a review shared by its unguessable, user-facing code."""
    normalized_code = review_code.strip().upper()
    if not normalized_code.startswith(REVIEW_CODE_PREFIX):
        normalized_code = REVIEW_CODE_PREFIX + normalized_code
    if not re.fullmatch(
        rf"{REVIEW_CODE_PREFIX}[{REVIEW_CODE_ALPHABET}]{{{REVIEW_CODE_LENGTH}}}",
        normalized_code,
    ):
        return None

    query = """
        SELECT lead_tenant, co_tenants, property_address, filename, review
        FROM tenancy_reviews
        WHERE id = {}
    """
    try:
        if config.DATABASE_URL:
            with psycopg.connect(config.DATABASE_URL) as connection:
                row = connection.execute(query.format("%s"), (normalized_code,)).fetchone()
        else:
            if not config.REVIEWS_DB_PATH.exists():
                return None
            with closing(sqlite3.connect(config.REVIEWS_DB_PATH, timeout=10)) as connection:
                row = connection.execute(query.format("?"), (normalized_code,)).fetchone()
    except psycopg.errors.UndefinedTable:
        return None
    except sqlite3.OperationalError as error:
        if "no such table" not in str(error):
            raise
        return None

    if row is None:
        return None
    lead_tenant, co_tenants, property_address, filename, review = row
    co_tenants = json.loads(co_tenants) if isinstance(co_tenants, str) else co_tenants
    review = json.loads(review) if isinstance(review, str) else review
    return {
        **review,
        "review_id": normalized_code,
        "case_code": normalized_code,
        "tenant_name": lead_tenant,
        "lead_tenant": lead_tenant,
        "co_tenants": co_tenants,
        "tenants": [
            {"name": lead_tenant, "role": "lead"},
            *({"name": name, "role": "co_tenant"} for name in co_tenants),
        ],
        "property_address": property_address,
        "filename": filename,
    }


def _save_postgres(
    review_id: str,
    lead_tenant: str,
    co_tenants: list[str],
    property_address: str,
    filename: str | None,
    review: dict[str, Any],
    owner_account_id: str | None,
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
                owner_account_id text,
                created_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        connection.execute(
            "ALTER TABLE tenancy_reviews ADD COLUMN IF NOT EXISTS owner_account_id text"
        )
        connection.execute(
            """
            INSERT INTO tenancy_reviews
                (id, lead_tenant, co_tenants, property_address, filename, review, owner_account_id)
            VALUES (%s, %s, %s::jsonb, %s, %s, %s::jsonb, %s)
            """,
            (
                review_id,
                lead_tenant,
                json.dumps(co_tenants),
                property_address,
                filename,
                json.dumps(review),
                owner_account_id,
            ),
        )


def _save_sqlite(
    review_id: str,
    lead_tenant: str,
    co_tenants: list[str],
    property_address: str,
    filename: str | None,
    review: dict[str, Any],
    owner_account_id: str | None,
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
                    owner_account_id text,
                    created_at text NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(tenancy_reviews)")
            }
            if "owner_account_id" not in columns:
                connection.execute(
                    "ALTER TABLE tenancy_reviews ADD COLUMN owner_account_id text"
                )
            connection.execute(
                """
                INSERT INTO tenancy_reviews
                    (id, lead_tenant, co_tenants, property_address, filename, review, owner_account_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    lead_tenant,
                    json.dumps(co_tenants),
                    property_address,
                    filename,
                    json.dumps(review),
                    owner_account_id,
                ),
            )
