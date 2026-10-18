"""Persist completed agreement reviews in PostgreSQL or a local SQLite database."""

import json
import sqlite3
import uuid
from contextlib import closing
from typing import Any

import psycopg

from app import config


def save_review(
    lead_tenant: str,
    co_tenants: list[str],
    property_address: str,
    filename: str | None,
    review: dict[str, Any],
) -> str:
    review_id = uuid.uuid4().hex
    if config.DATABASE_URL:
        _save_postgres(
            review_id, lead_tenant, co_tenants, property_address, filename, review
        )
    else:
        _save_sqlite(review_id, lead_tenant, co_tenants, property_address, filename, review)
    return review_id


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
