"""Tenancy calendar items and opted-in email reminders."""

from __future__ import annotations

import calendar as month_calendar
import logging
import smtplib
import ssl
import uuid
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any
from zoneinfo import ZoneInfo

from app import config, tenancies

logger = logging.getLogger(__name__)


def _month_occurrence(first_due: date, year: int, month: int) -> date | None:
    day = min(first_due.day, month_calendar.monthrange(year, month)[1])
    occurrence = date(year, month, day)
    return occurrence if occurrence >= first_due else None


def list_items(
    profile_id: str, account_id: str, month_start: date, month_end: date
) -> list[dict[str, Any]]:
    with closing(tenancies._connection()) as connection:
        tenancies._ensure_schema(connection)
        rows = connection.execute(
            tenancies._sql(
                """
                SELECT c.id, c.title, c.kind, c.due_date, c.recurrence, c.amount
                FROM tenancy_calendar_items c
                JOIN tenancy_members m ON m.profile_id = c.profile_id
                WHERE c.profile_id = ? AND m.account_id = ?
                ORDER BY c.due_date, c.title
                """
            ),
            (profile_id, account_id),
        ).fetchall()

    events = []
    for row in rows:
        due_date = date.fromisoformat(row["due_date"])
        occurrences = (
            [_month_occurrence(due_date, month_start.year, month_start.month)]
            if row["recurrence"] == "monthly"
            else [due_date]
        )
        for occurrence in occurrences:
            if occurrence and month_start <= occurrence < month_end:
                events.append(
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "kind": row["kind"],
                        "due_date": occurrence.isoformat(),
                        "recurrence": row["recurrence"],
                        "amount": row["amount"],
                    }
                )
    return sorted(events, key=lambda item: (item["due_date"], item["title"].casefold()))


def add_item(
    profile_id: str,
    account_id: str,
    title: str,
    kind: str,
    due_date: date,
    recurrence: str,
    amount: float | None,
) -> dict[str, Any]:
    if kind not in {"rent", "bill"}:
        raise ValueError("Choose rent or bill.")
    if recurrence not in {"once", "monthly"}:
        raise ValueError("Choose a one-off or monthly due date.")
    if due_date < date.today():
        raise ValueError("Choose a due date that is today or in the future.")

    item_id = uuid.uuid4().hex
    with closing(tenancies._connection()) as connection:
        tenancies._ensure_schema(connection)
        with tenancies._transaction(connection):
            member = connection.execute(
                tenancies._sql(
                    "SELECT 1 FROM tenancy_members WHERE profile_id = ? AND account_id = ?"
                ),
                (profile_id, account_id),
            ).fetchone()
            if member is None:
                raise PermissionError("You are not a member of this tenancy.")
            connection.execute(
                tenancies._sql(
                    """
                    INSERT INTO tenancy_calendar_items
                        (id, profile_id, created_by, title, kind, due_date, recurrence, amount, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """
                ),
                (
                    item_id,
                    profile_id,
                    account_id,
                    title.strip(),
                    kind,
                    due_date.isoformat(),
                    recurrence,
                    amount,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
    return {
        "id": item_id,
        "title": title.strip(),
        "kind": kind,
        "due_date": due_date.isoformat(),
        "recurrence": recurrence,
        "amount": amount,
    }


def delete_item(profile_id: str, item_id: str, account_id: str) -> bool:
    with closing(tenancies._connection()) as connection:
        tenancies._ensure_schema(connection)
        with tenancies._transaction(connection):
            cursor = connection.execute(
                tenancies._sql(
                    """
                    DELETE FROM tenancy_calendar_items
                    WHERE id = ? AND profile_id = ?
                      AND EXISTS (
                        SELECT 1 FROM tenancy_members
                        WHERE profile_id = ? AND account_id = ?
                      )
                    """
                ),
                (item_id, profile_id, profile_id, account_id),
            )
            return cursor.rowcount > 0


def get_email_preference(account_id: str) -> bool:
    with closing(tenancies._connection()) as connection:
        tenancies._ensure_schema(connection)
        row = connection.execute(
            tenancies._sql(
                "SELECT email_reminders_enabled FROM flatmate_notification_preferences WHERE account_id = ?"
            ),
            (account_id,),
        ).fetchone()
    return bool(row["email_reminders_enabled"]) if row else False


def set_email_preference(account_id: str, enabled: bool) -> None:
    with closing(tenancies._connection()) as connection:
        tenancies._ensure_schema(connection)
        with tenancies._transaction(connection):
            connection.execute(
                tenancies._sql(
                    """
                    INSERT INTO flatmate_notification_preferences (account_id, email_reminders_enabled)
                    VALUES (?, ?)
                    ON CONFLICT (account_id) DO UPDATE SET email_reminders_enabled = excluded.email_reminders_enabled
                    """
                ),
                (account_id, enabled),
            )


def _send_email(recipient: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = config.REMINDER_FROM_EMAIL
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    if config.REMINDER_SMTP_USE_SSL:
        with smtplib.SMTP_SSL(
            config.REMINDER_SMTP_HOST,
            config.REMINDER_SMTP_PORT,
            context=ssl.create_default_context(),
            timeout=20,
        ) as smtp:
            if config.REMINDER_SMTP_USERNAME:
                smtp.login(config.REMINDER_SMTP_USERNAME, config.REMINDER_SMTP_PASSWORD or "")
            smtp.send_message(message)
    else:
        with smtplib.SMTP(
            config.REMINDER_SMTP_HOST, config.REMINDER_SMTP_PORT, timeout=20
        ) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if config.REMINDER_SMTP_USERNAME:
                smtp.login(config.REMINDER_SMTP_USERNAME, config.REMINDER_SMTP_PASSWORD or "")
            smtp.send_message(message)


def _eligible_occurrence(item: Any, target_date: date) -> bool:
    first_due = date.fromisoformat(item["due_date"])
    if item["recurrence"] == "once":
        return first_due == target_date
    if first_due > target_date:
        return False
    occurrence = _month_occurrence(first_due, target_date.year, target_date.month)
    return occurrence == target_date


def send_due_reminders(today: date | None = None) -> int:
    """Send reminders for due dates two days away; safe to call repeatedly."""
    if not config.EMAIL_REMINDERS_CONFIGURED:
        return 0
    target_date = (today or datetime.now(ZoneInfo("Europe/London")).date()) + timedelta(days=2)
    with closing(tenancies._connection()) as connection:
        tenancies._ensure_schema(connection)
        rows = connection.execute(
            """
            SELECT c.id, c.profile_id, c.title, c.kind, c.due_date, c.recurrence, c.amount,
                   a.id AS account_id, a.email, p.property_address
            FROM tenancy_calendar_items c
            JOIN tenancy_members m ON m.profile_id = c.profile_id
            JOIN flatmate_accounts a ON a.id = m.account_id
            JOIN flatmate_notification_preferences n ON n.account_id = a.id
            JOIN tenancy_profiles p ON p.id = c.profile_id
            WHERE n.email_reminders_enabled
            """
        ).fetchall()

    reminders = [row for row in rows if _eligible_occurrence(row, target_date)]
    sent = 0
    for item in reminders:
        now = datetime.now(timezone.utc).isoformat()
        stale_before = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        with closing(tenancies._connection()) as connection:
            tenancies._ensure_schema(connection)
            with tenancies._transaction(connection):
                connection.execute(
                    tenancies._sql(
                        """
                        INSERT INTO tenancy_calendar_reminders
                            (item_id, account_id, due_date, status, updated_at)
                        VALUES (?, ?, ?, 'pending', ?)
                        ON CONFLICT (item_id, account_id, due_date) DO NOTHING
                        """
                    ),
                    (item["id"], item["account_id"], target_date.isoformat(), now),
                )
                claim = connection.execute(
                    tenancies._sql(
                        """
                        UPDATE tenancy_calendar_reminders
                        SET status = 'sending', updated_at = ?
                        WHERE item_id = ? AND account_id = ? AND due_date = ?
                          AND (status = 'pending' OR (status = 'sending' AND updated_at < ?))
                        """
                    ),
                    (
                        now,
                        item["id"],
                        item["account_id"],
                        target_date.isoformat(),
                        stale_before,
                    ),
                )
                if claim.rowcount == 0:
                    continue

        amount = f" (£{item['amount']:.2f})" if item["amount"] is not None else ""
        subject = f"Due in 2 days: {item['title']}"
        body = (
            f"Hi,\n\nA reminder that {item['title']}{amount} is due on "
            f"{target_date.strftime('%d %B %Y').lstrip('0')} for {item['property_address']}.\n\n"
            "This is an automated reminder from FlatMate."
        )
        try:
            _send_email(item["email"], subject, body)
        except (OSError, smtplib.SMTPException) as error:
            logger.exception(
                "Could not send calendar reminder %s to account %s",
                item["id"],
                item["account_id"],
            )
            with closing(tenancies._connection()) as connection:
                tenancies._ensure_schema(connection)
                with tenancies._transaction(connection):
                    connection.execute(
                        tenancies._sql(
                            """
                            UPDATE tenancy_calendar_reminders
                            SET status = 'pending', updated_at = ?
                            WHERE item_id = ? AND account_id = ? AND due_date = ? AND status = 'sending'
                            """
                        ),
                        (datetime.now(timezone.utc).isoformat(), item["id"], item["account_id"], target_date.isoformat()),
                    )
            continue

        with closing(tenancies._connection()) as connection:
            tenancies._ensure_schema(connection)
            with tenancies._transaction(connection):
                connection.execute(
                    tenancies._sql(
                        """
                        UPDATE tenancy_calendar_reminders
                        SET status = 'sent', updated_at = ?
                        WHERE item_id = ? AND account_id = ? AND due_date = ? AND status = 'sending'
                        """
                    ),
                    (datetime.now(timezone.utc).isoformat(), item["id"], item["account_id"], target_date.isoformat()),
                )
        sent += 1
    return sent
