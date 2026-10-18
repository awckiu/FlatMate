"""Shared tenancy-profile persistence. Logins live in app.auth (the users table); db/schema.sql owns the tables."""

from __future__ import annotations

import json
import secrets
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import date, datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app import cases, config

HOUSEHOLD_MODES = {"single", "friends"}


def _connection():
    if config.DATABASE_URL:
        return psycopg.connect(config.DATABASE_URL, row_factory=dict_row)
    config.REVIEWS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(config.REVIEWS_DB_PATH, timeout=10)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def _transaction(connection):
    """Commit on success, roll back on error. sqlite3's `with connection` does this, but
    psycopg's closes the connection, which broke every later query on Tiger Data."""
    if isinstance(connection, sqlite3.Connection):
        with connection:
            yield
        return
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _sql(query: str) -> str:
    return query.replace("?", "%s") if config.DATABASE_URL else query


def _ensure_schema(connection) -> None:
    if config.DATABASE_URL:
        # Tiger Data: db/schema.sql is the only definition of these tables.
        cases.ensure_schema()
        return
    # Everything below is the SQLite stand-in used by the tests (no DATABASE_URL). Keep it in
    # step with db/schema.sql.
    connection.execute(
        _sql(
            """
            CREATE TABLE IF NOT EXISTS users (
                id text PRIMARY KEY,
                email text NOT NULL UNIQUE,
                display_name text NOT NULL,
                password_hash text
            )
            """
        )
    )
    connection.execute(
        _sql(
            """
            CREATE TABLE IF NOT EXISTS tenancy_profiles (
                id text PRIMARY KEY,
                code text UNIQUE,
                owner_account_id text NOT NULL REFERENCES users(id),
                household_mode text NOT NULL,
                hmo_status text NOT NULL,
                property_address text NOT NULL,
                lead_tenant text NOT NULL,
                co_tenants text NOT NULL,
                current_tenant text NOT NULL,
                "current_role" text NOT NULL,
                agreement_signed integer NOT NULL DEFAULT 0,
                signed_on text,
                tenancy_start text,
                is_active integer NOT NULL DEFAULT 0,
                review_id text NOT NULL,
                review text NOT NULL,
                created_at text NOT NULL
            )
            """
        )
    )
    profile_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(tenancy_profiles)").fetchall()
    }
    if "review_id" not in profile_columns:
        connection.execute("ALTER TABLE tenancy_profiles ADD COLUMN review_id text")
    for column in ("moved_in_at", "closed_at"):
        if column not in profile_columns:
            connection.execute(f"ALTER TABLE tenancy_profiles ADD COLUMN {column} text")

    connection.execute(
        _sql(
            """
            CREATE TABLE IF NOT EXISTS tenancy_members (
                profile_id text NOT NULL REFERENCES tenancy_profiles(id) ON DELETE CASCADE,
                account_id text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name text NOT NULL,
                role text NOT NULL,
                is_current integer NOT NULL DEFAULT 1,
                PRIMARY KEY (profile_id, account_id)
            )
            """
        )
    )
    connection.execute(
        _sql(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS one_current_tenancy_per_account
            ON tenancy_members(account_id) WHERE is_current = 1
            """
        )
    )
    connection.execute(
        _sql(
            """
            CREATE TABLE IF NOT EXISTS tenancy_chat_messages (
                id text PRIMARY KEY,
                profile_id text NOT NULL REFERENCES tenancy_profiles(id) ON DELETE CASCADE,
                account_id text NOT NULL REFERENCES users(id),
                user_message text NOT NULL,
                assistant_message text NOT NULL,
                landlord_draft text NOT NULL,
                created_at text NOT NULL
            )
            """
        )
    )
    connection.execute(
        _sql(
            """
            CREATE TABLE IF NOT EXISTS tenancy_clause_status (
                profile_id text NOT NULL REFERENCES tenancy_profiles(id) ON DELETE CASCADE,
                issue_key text NOT NULL,
                status text NOT NULL CHECK (status IN ('open', 'resolved')),
                update_note text NOT NULL,
                updated_by text NOT NULL,
                updated_at text NOT NULL,
                PRIMARY KEY (profile_id, issue_key)
            )
            """
        )
    )
    connection.execute(
        _sql(
            """
            CREATE TABLE IF NOT EXISTS tenancy_photos (
                id text PRIMARY KEY,
                profile_id text NOT NULL REFERENCES tenancy_profiles(id) ON DELETE CASCADE,
                account_id text NOT NULL REFERENCES users(id),
                room text NOT NULL,
                note text NOT NULL,
                data blob NOT NULL,
                media_type text NOT NULL,
                uploaded_at text NOT NULL
            )
            """
        )
    )
    connection.execute(
        _sql(
            """
            CREATE TABLE IF NOT EXISTS tenancy_calendar_items (
                id text PRIMARY KEY,
                profile_id text NOT NULL REFERENCES tenancy_profiles(id) ON DELETE CASCADE,
                created_by text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title text NOT NULL,
                kind text NOT NULL,
                due_date text NOT NULL,
                recurrence text NOT NULL,
                amount real,
                created_at text NOT NULL
            )
            """
        )
    )
    connection.execute(
        _sql(
            """
            CREATE TABLE IF NOT EXISTS flatmate_notification_preferences (
                account_id text PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                email_reminders_enabled boolean NOT NULL DEFAULT false
            )
            """
        )
    )
    connection.execute(
        _sql(
            """
            CREATE TABLE IF NOT EXISTS tenancy_calendar_reminders (
                item_id text NOT NULL REFERENCES tenancy_calendar_items(id) ON DELETE CASCADE,
                account_id text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                due_date text NOT NULL,
                status text NOT NULL,
                updated_at text NOT NULL,
                PRIMARY KEY (item_id, account_id, due_date)
            )
            """
        )
    )
    connection.commit()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_household(
    household_mode: str, hmo_status: str, lead_tenant: str, co_tenants: list[str]
) -> None:
    """Rules shared by the analysis and the tenancy profile, so a review that is accepted
    can always be turned into a tenancy."""
    if household_mode not in HOUSEHOLD_MODES:
        raise ValueError("Choose a valid household setup.")
    if hmo_status not in {"yes", "no", "unknown", "not_applicable"}:
        raise ValueError("Choose whether the property is an HMO, not an HMO, or unknown.")
    if household_mode == "friends" and hmo_status == "not_applicable":
        raise ValueError("Choose an HMO status when living with friends.")
    if household_mode == "single" and co_tenants:
        raise ValueError("A solo tenancy must have one lead tenant and no co-tenants.")
    if any(not name.strip() for name in co_tenants):
        raise ValueError("Co-tenant names cannot be blank.")
    if len({name.casefold() for name in co_tenants}) != len(co_tenants):
        raise ValueError("Enter each co-tenant only once.")
    if lead_tenant.casefold() in {name.casefold() for name in co_tenants}:
        raise ValueError("The lead tenant should not be repeated in the co-tenant list.")


def create_profile(
    account: dict[str, str],
    household_mode: str,
    hmo_status: str,
    property_address: str,
    lead_tenant: str,
    co_tenants: list[str],
    current_tenant: str,
    current_role: str,
    review_id: str,
    review: dict[str, Any],
    agreement_signed: bool = False,
    signed_on: str | None = None,
    tenancy_start: str | None = None,
) -> dict[str, Any]:
    validate_household(household_mode, hmo_status, lead_tenant, co_tenants)
    if current_role not in {"lead", "co_tenant"}:
        raise ValueError("Choose whether you are the lead tenant or a co-tenant.")
    if current_role == "lead" and current_tenant.casefold() != lead_tenant.casefold():
        raise ValueError("The signed-in tenant's name must match the lead tenant name.")
    if current_role == "co_tenant" and current_tenant.casefold() not in {
        name.casefold() for name in co_tenants
    }:
        raise ValueError("The signed-in tenant's name must appear in the co-tenant list.")
    if household_mode == "single" and current_role != "lead":
        raise ValueError("A solo tenancy must have one lead tenant and no co-tenants.")
    _validate_signing(agreement_signed, signed_on, tenancy_start)

    profile_id = uuid.uuid4().hex
    with closing(_connection()) as connection:
        _ensure_schema(connection)
        existing = connection.execute(
            _sql(
                """
                SELECT p.id FROM tenancy_profiles p JOIN tenancy_members m ON m.profile_id = p.id
                WHERE p.review_id = ? AND m.account_id = ?
                """
            ),
            (review_id, account["id"]),
        ).fetchone()
        if existing is not None:
            # The same review submitted again (e.g. a retry) reuses this account's tenancy.
            profile = get_profile(existing["id"], account["id"])
            if profile is not None:
                return profile
        with _transaction(connection):
            connection.execute(
                _sql("UPDATE tenancy_members SET is_current = 0 WHERE account_id = ?"),
                (account["id"],),
            )
            connection.execute(
                _sql(
                    """
                    INSERT INTO tenancy_profiles
                        (id, owner_account_id, household_mode, hmo_status, property_address,
                         lead_tenant, co_tenants, current_tenant, "current_role",
                          agreement_signed, signed_on, tenancy_start, review_id, review, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """
                ),
                (
                    profile_id,
                    account["id"],
                    household_mode,
                    hmo_status,
                    property_address.strip(),
                    lead_tenant.strip(),
                    json.dumps(co_tenants),
                    current_tenant.strip(),
                    current_role,
                    int(agreement_signed),
                    signed_on,
                    tenancy_start,
                    review_id,
                    json.dumps(review),
                    _timestamp(),
                ),
            )
            connection.execute(
                _sql(
                    "INSERT INTO tenancy_members (profile_id, account_id, name, role) VALUES (?, ?, ?, ?)"
                ),
                (profile_id, account["id"], current_tenant.strip(), current_role),
            )
    profile = get_profile(profile_id, account["id"])
    if profile is None:
        raise RuntimeError("The tenancy profile could not be loaded after creation.")
    return profile


def refresh_review(review_id: str, review: dict[str, Any]) -> None:
    """After the agreement is amended, give every tenancy built on that review the new
    findings. Saved issue statuses are keyed by the old findings' positions, so they reset."""
    with closing(_connection()) as connection:
        _ensure_schema(connection)
        with _transaction(connection):
            connection.execute(
                _sql(
                    """
                    DELETE FROM tenancy_clause_status
                    WHERE profile_id IN (SELECT id FROM tenancy_profiles WHERE review_id = ?)
                    """
                ),
                (review_id,),
            )
            connection.execute(
                _sql("UPDATE tenancy_profiles SET review = ? WHERE review_id = ?"),
                (json.dumps(review), review_id),
            )


def _validate_signing(agreement_signed: bool, signed_on: str | None, tenancy_start: str | None) -> None:
    signed_date = date.fromisoformat(signed_on) if signed_on else None
    start_date = date.fromisoformat(tenancy_start) if tenancy_start else None
    if agreement_signed and not signed_on:
        raise ValueError("Enter the date the agreement was signed.")
    if not agreement_signed and signed_on:
        raise ValueError("Only enter a signing date after the agreement has been signed.")
    if agreement_signed and not tenancy_start:
        raise ValueError("Enter the tenancy start date so the tenancy code can be issued.")
    if signed_date and start_date and start_date < signed_date:
        raise ValueError("The tenancy cannot start before the agreement was signed.")


def _activate_due_profiles(connection, account_id: str | None = None) -> None:
    today = date.today().isoformat()
    conditions = "agreement_signed = 1 AND tenancy_start <= ? AND code IS NULL"
    params: tuple[Any, ...] = (today,)
    if account_id:
        conditions += " AND owner_account_id = ?"
        params += (account_id,)
    due = connection.execute(
        _sql(f"SELECT id FROM tenancy_profiles WHERE {conditions}"), params
    ).fetchall()
    for row in due:
        code = "HOME-" + "".join(
            secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(10)
        )
        existing = connection.execute(
            _sql("SELECT 1 FROM tenancy_profiles WHERE code = ?"), (code,)
        ).fetchone()
        if existing is None:
            connection.execute(
                _sql(
                    "UPDATE tenancy_profiles SET code = ?, is_active = 1 WHERE id = ? AND code IS NULL"
                ),
                (code, row["id"]),
            )


def get_profile(profile_id: str, account_id: str) -> dict[str, Any] | None:
    with closing(_connection()) as connection:
        _ensure_schema(connection)
        with _transaction(connection):
            _activate_due_profiles(connection, account_id)
        row = connection.execute(
            _sql(
                """
                SELECT p.*, m.role AS member_role, m.is_current AS is_current_for_user
                FROM tenancy_profiles p
                JOIN tenancy_members m ON m.profile_id = p.id
                WHERE p.id = ? AND m.account_id = ?
                """
            ),
            (profile_id, account_id),
        ).fetchone()
    return _decode_profile(row) if row else None


def get_profile_by_code(code: str, account_id: str) -> dict[str, Any] | None:
    normalized = code.strip().upper()
    with closing(_connection()) as connection:
        _ensure_schema(connection)
        with _transaction(connection):
            _activate_due_profiles(connection)
        row = connection.execute(
            _sql(
                """
                SELECT p.*, m.role AS member_role, m.is_current AS is_current_for_user
                FROM tenancy_profiles p
                JOIN tenancy_members m ON m.profile_id = p.id
                WHERE p.code = ? AND p.is_active = 1 AND m.account_id = ?
                """
            ),
            (normalized, account_id),
        ).fetchone()
    return _decode_profile(row) if row else None


def _decode_profile(row) -> dict[str, Any]:
    result = dict(row)
    result["co_tenants"] = json.loads(result["co_tenants"])
    result["review"] = json.loads(result["review"])
    for key in ("agreement_signed", "is_active"):
        result[key] = bool(result[key])
    result["is_current"] = bool(result.pop("is_current_for_user"))
    result["role"] = result.pop("member_role")
    return result


def list_profiles(account_id: str) -> list[dict[str, Any]]:
    with closing(_connection()) as connection:
        _ensure_schema(connection)
        with _transaction(connection):
            _activate_due_profiles(connection)
        rows = connection.execute(
            _sql(
                """
                SELECT p.*, m.role AS member_role, m.is_current AS is_current_for_user
                FROM tenancy_profiles p
                JOIN tenancy_members m ON m.profile_id = p.id
                WHERE m.account_id = ?
                ORDER BY p.created_at DESC
                """
            ),
            (account_id,),
        ).fetchall()
    return [_decode_profile(row) for row in rows]


def join_profile(code: str, account: dict[str, str], tenant_name: str, role: str) -> dict[str, Any] | None:
    normalized_code = code.strip().upper()
    normalized_name = tenant_name.strip()
    if role not in {"lead", "co_tenant"} or not normalized_name:
        raise ValueError("Enter your name and choose your role on the agreement.")
    with closing(_connection()) as connection:
        _ensure_schema(connection)
        with _transaction(connection):
            _activate_due_profiles(connection)
            # Co-tenants can join with the HOME- tenancy code (issued on the start date) or with the
            # FLAT- review code the lead tenant sees from the first review, so they can join before move-in.
            row = connection.execute(
                _sql(
                    """
                    SELECT * FROM tenancy_profiles
                    WHERE code = ? OR review_id = ?
                    ORDER BY is_active DESC, created_at DESC
                    LIMIT 1
                    """
                ),
                (normalized_code, normalized_code),
            ).fetchone()
            if row is None:
                return None
            already_member = connection.execute(
                _sql("SELECT 1 FROM tenancy_members WHERE profile_id = ? AND account_id = ?"),
                (row["id"], account["id"]),
            ).fetchone()
            listed_names = (
                [row["lead_tenant"]]
                if role == "lead"
                else json.loads(row["co_tenants"])
            )
            if normalized_name.casefold() not in {name.casefold() for name in listed_names}:
                raise ValueError("That name and role do not match a tenant listed on this tenancy.")
            connection.execute(
                _sql("UPDATE tenancy_members SET is_current = 0 WHERE account_id = ?"),
                (account["id"],),
            )
            if already_member:
                connection.execute(
                    _sql("UPDATE tenancy_members SET is_current = 1 WHERE profile_id = ? AND account_id = ?"),
                    (row["id"], account["id"]),
                )
            else:
                connection.execute(
                    _sql(
                        """
                        INSERT INTO tenancy_members (profile_id, account_id, name, role, is_current)
                        VALUES (?, ?, ?, ?, 1)
                        """
                    ),
                    (row["id"], account["id"], normalized_name, role),
                )
    return get_profile(row["id"], account["id"])


def update_signing(
    profile_id: str,
    account_id: str,
    agreement_signed: bool,
    signed_on: str | None,
    tenancy_start: str | None,
) -> dict[str, Any] | None:
    _validate_signing(agreement_signed, signed_on, tenancy_start)
    with closing(_connection()) as connection:
        _ensure_schema(connection)
        with _transaction(connection):
            row = connection.execute(
                _sql(
                    """
                    SELECT id, agreement_signed, tenancy_start, is_active
                    FROM tenancy_profiles
                    WHERE id = ? AND owner_account_id = ?
                    """
                ),
                (profile_id, account_id),
            ).fetchone()
            if row is None:
                return None
            if row["agreement_signed"] and not agreement_signed:
                raise ValueError("A signed agreement cannot be changed back to unsigned.")
            if row["is_active"] and tenancy_start != row["tenancy_start"]:
                raise ValueError("The tenancy start date cannot be changed after its access code is issued.")
            connection.execute(
                _sql(
                    """
                    UPDATE tenancy_profiles
                    SET agreement_signed = ?, signed_on = ?, tenancy_start = ?
                    WHERE id = ? AND owner_account_id = ?
                    """
                ),
                (
                    int(agreement_signed),
                    signed_on if agreement_signed else None,
                    tenancy_start,
                    profile_id,
                    account_id,
                ),
            )
            _activate_due_profiles(connection, account_id)
    return get_profile(profile_id, account_id)


def is_member_of_review(review_id: str, account_id: str) -> bool:
    """True if the account belongs to a tenancy made from this FLAT- case code."""
    with closing(_connection()) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            _sql(
                """
                SELECT 1 FROM tenancy_profiles p
                JOIN tenancy_members m ON m.profile_id = p.id
                WHERE p.review_id = ? AND m.account_id = ?
                """
            ),
            (review_id, account_id),
        ).fetchone()
    return row is not None


def update_progress(
    profile_id: str,
    account_id: str,
    moved_in: bool | None,
    closed: bool | None,
) -> dict[str, Any] | None:
    """Mark the household as moved in or the tenancy as closed. Any member can do this."""
    with closing(_connection()) as connection:
        _ensure_schema(connection)
        with _transaction(connection):
            member = connection.execute(
                _sql("SELECT 1 FROM tenancy_members WHERE profile_id = ? AND account_id = ?"),
                (profile_id, account_id),
            ).fetchone()
            if member is None:
                return None
            for column, value in (("moved_in_at", moved_in), ("closed_at", closed)):
                if value is None:
                    continue
                connection.execute(
                    _sql(f"UPDATE tenancy_profiles SET {column} = ? WHERE id = ?"),
                    (_timestamp() if value else None, profile_id),
                )
    return get_profile(profile_id, account_id)


def add_chat_message(
    profile_id: str,
    account_id: str,
    user_message: str,
    assistant_message: str,
    landlord_draft: str,
) -> str:
    message_id = uuid.uuid4().hex
    with closing(_connection()) as connection:
        _ensure_schema(connection)
        with _transaction(connection):
            member = connection.execute(
                _sql(
                    """
                    SELECT p.agreement_signed
                    FROM tenancy_profiles p JOIN tenancy_members m ON m.profile_id = p.id
                    WHERE p.id = ? AND m.account_id = ?
                    """
                ),
                (profile_id, account_id),
            ).fetchone()
            if member is None or not member["agreement_signed"]:
                raise PermissionError("The tenancy buddy is available after the agreement is signed.")
            connection.execute(
                _sql(
                    """
                    INSERT INTO tenancy_chat_messages
                        (id, profile_id, account_id, user_message, assistant_message,
                         landlord_draft, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """
                ),
                (
                    message_id,
                    profile_id,
                    account_id,
                    user_message,
                    assistant_message,
                    landlord_draft,
                    _timestamp(),
                ),
            )
    return message_id


def list_chat_messages(profile_id: str, account_id: str) -> list[dict[str, Any]]:
    with closing(_connection()) as connection:
        _ensure_schema(connection)
        rows = connection.execute(
            _sql(
                """
                SELECT c.id, c.user_message, c.assistant_message, c.landlord_draft, c.created_at
                FROM tenancy_chat_messages c
                JOIN tenancy_members m ON m.profile_id = c.profile_id
                WHERE c.profile_id = ? AND m.account_id = ?
                ORDER BY c.created_at
                """
            ),
            (profile_id, account_id),
        ).fetchall()
    return [dict(row) for row in rows]


def _clause_issue_catalog(review: dict[str, Any]) -> list[dict[str, str]]:
    issues = []
    for index, flag in enumerate(review.get("flags", [])):
        if not isinstance(flag, dict):
            continue
        reference = str(flag.get("clause_ref") or "").strip()
        excerpt = str(flag.get("clause_excerpt") or "").strip()
        explanation = str(flag.get("explanation") or "").strip()
        issues.append(
            {
                "issue_key": f"flag-{index}",
                "kind": str(flag.get("kind") or "review_flag"),
                "clause_ref": reference,
                "title": f"Clause {reference}" if reference else "Agreement point",
                "description": explanation or excerpt or "Flagged agreement clause",
            }
        )
    for index, item in enumerate(review.get("need_to_knows", [])):
        if not isinstance(item, dict):
            continue
        reference = str(item.get("clause_ref") or "").strip()
        topic = str(item.get("topic") or "").strip()
        issues.append(
            {
                "issue_key": f"need-to-know-{index}",
                "kind": "need_to_know",
                "clause_ref": reference,
                "title": topic or (f"Clause {reference}" if reference else "Agreement point"),
                "description": str(item.get("text") or topic or "Agreement point to keep in mind"),
            }
        )
    return issues


def list_clause_issues(profile_id: str, account_id: str) -> list[dict[str, Any]]:
    profile = get_profile(profile_id, account_id)
    if profile is None:
        raise PermissionError("You are not a member of this tenancy.")
    issues = _clause_issue_catalog(profile["review"])
    with closing(_connection()) as connection:
        _ensure_schema(connection)
        rows = connection.execute(
            _sql(
                """
                SELECT issue_key, status, update_note, updated_at
                FROM tenancy_clause_status
                WHERE profile_id = ?
                """
            ),
            (profile_id,),
        ).fetchall()
    saved = {row["issue_key"]: dict(row) for row in rows}
    for issue in issues:
        status = saved.get(issue["issue_key"])
        issue["status"] = status["status"] if status else "unconfirmed"
        issue["update_note"] = status["update_note"] if status else ""
        issue["updated_at"] = status["updated_at"] if status else None
    return issues


def record_clause_status_updates(
    profile_id: str,
    account_id: str,
    review: dict[str, Any],
    updates: list[dict[str, str]],
    user_message: str,
) -> list[dict[str, str]]:
    catalog = {issue["issue_key"]: issue for issue in _clause_issue_catalog(review)}
    normalized_message = " ".join(user_message.casefold().split())
    accepted: list[dict[str, str]] = []
    with closing(_connection()) as connection:
        _ensure_schema(connection)
        with connection:
            member = connection.execute(
                _sql(
                    "SELECT 1 FROM tenancy_members WHERE profile_id = ? AND account_id = ?"
                ),
                (profile_id, account_id),
            ).fetchone()
            if member is None:
                raise PermissionError("You are not a member of this tenancy.")
            for update in updates:
                issue_key = update.get("issue_key", "")
                status = update.get("status", "")
                evidence = " ".join(update.get("evidence", "").casefold().split())
                issue = catalog.get(issue_key)
                if (
                    issue is None
                    or status not in {"open", "resolved"}
                    or not evidence
                    or evidence not in normalized_message
                ):
                    continue
                updated_at = _timestamp()
                connection.execute(
                    _sql(
                        """
                        INSERT INTO tenancy_clause_status
                            (profile_id, issue_key, status, update_note, updated_by, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT (profile_id, issue_key) DO UPDATE SET
                            status = excluded.status,
                            update_note = excluded.update_note,
                            updated_by = excluded.updated_by,
                            updated_at = excluded.updated_at
                        """
                    ),
                    (profile_id, issue_key, status, update["evidence"].strip(), account_id, updated_at),
                )
                accepted.append(
                    {
                        "issue_key": issue_key,
                        "title": issue["title"],
                        "status": status,
                    }
                )
    return accepted


def add_photo(
    profile_id: str,
    account_id: str,
    room: str,
    note: str,
    data: bytes,
    media_type: str,
) -> str:
    photo_id = uuid.uuid4().hex
    with closing(_connection()) as connection:
        _ensure_schema(connection)
        with _transaction(connection):
            member = connection.execute(
                _sql(
                    """
                    SELECT p.is_active
                    FROM tenancy_profiles p JOIN tenancy_members m ON m.profile_id = p.id
                    WHERE p.id = ? AND m.account_id = ?
                    """
                ),
                (profile_id, account_id),
            ).fetchone()
            if member is None or not member["is_active"]:
                raise PermissionError("Move-in photo uploads unlock when the tenancy starts.")
            connection.execute(
                _sql(
                    """
                    INSERT INTO tenancy_photos
                        (id, profile_id, account_id, room, note, data, media_type, uploaded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """
                ),
                (
                    photo_id,
                    profile_id,
                    account_id,
                    room,
                    note,
                    data,
                    media_type,
                    _timestamp(),
                ),
            )
    return photo_id


def list_photos(profile_id: str, account_id: str) -> list[dict[str, Any]]:
    with closing(_connection()) as connection:
        _ensure_schema(connection)
        rows = connection.execute(
            _sql(
                """
                SELECT p.id, p.room, p.note, p.media_type, p.uploaded_at
                FROM tenancy_photos p JOIN tenancy_members m ON m.profile_id = p.profile_id
                WHERE p.profile_id = ? AND m.account_id = ?
                ORDER BY p.uploaded_at DESC
                """
            ),
            (profile_id, account_id),
        ).fetchall()
    return [dict(row) for row in rows]


def get_photo(profile_id: str, photo_id: str, account_id: str) -> dict[str, Any] | None:
    with closing(_connection()) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            _sql(
                """
                SELECT p.data, p.media_type
                FROM tenancy_photos p JOIN tenancy_members m ON m.profile_id = p.profile_id
                WHERE p.profile_id = ? AND p.id = ? AND m.account_id = ?
                """
            ),
            (profile_id, photo_id, account_id),
        ).fetchone()
    return dict(row) if row else None


def delete_photo(profile_id: str, photo_id: str, account_id: str) -> None:
    with closing(_connection()) as connection:
        _ensure_schema(connection)
        with _transaction(connection):
            connection.execute(
                _sql(
                    """
                    DELETE FROM tenancy_photos
                    WHERE profile_id = ? AND id = ?
                      AND EXISTS (
                        SELECT 1 FROM tenancy_members
                        WHERE profile_id = ? AND account_id = ?
                      )
                    """
                ),
                (profile_id, photo_id, profile_id, account_id),
            )
