"""Saved analyses in Tiger Data: one case per tenancy, reopened with its case code."""

import re
import secrets

import psycopg

from app import config
from app.pipeline import DISCLAIMER

CODE_PREFIX = "FLAT-"
# No 0/O/1/I/L so codes are easy to read out and type.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
# 10 characters (about 8e14 combinations): the code is the only thing protecting a case, which
# anyone holding it can open without logging in. Older 6-character codes still work.
CODE_LENGTH = 10
_schema_applied = False


def new_code() -> str:
    return CODE_PREFIX + "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def normalize_code(code: str) -> str:
    code = code.strip().upper()
    return code if code.startswith(CODE_PREFIX) else CODE_PREFIX + code


def connect() -> psycopg.Connection:
    global _schema_applied
    conn = psycopg.connect(config.DATABASE_URL)
    if not _schema_applied:
        with conn.transaction():
            conn.execute(config.SCHEMA_PATH.read_text(encoding="utf-8"))
        _schema_applied = True
    return conn


def ensure_schema() -> None:
    """Apply db/schema.sql if this process hasn't yet (the tenancy tables live there too)."""
    if not _schema_applied:
        connect().close()


def save_case(
    conn: psycopg.Connection,
    *,
    lead_tenant: str,
    co_tenants: list[str],
    property_address: str,
    filename: str | None,
    agreement_text: str,
    clauses: list[dict],
    result: dict,
    user_id: str | None = None,
    pdf: bytes | None = None,
    current_tenant: str | None = None,
) -> str:
    """Save one analysis as a new case and return its code. A logged-in uploader is
    linked to the tenant row for their own name (the lead unless current_tenant is a
    co-tenant), so the case appears in their account with the right role."""
    for _ in range(5):
        code = new_code()
        try:
            with conn.transaction():
                _insert_case(
                    conn, code, lead_tenant, co_tenants, property_address,
                    filename, agreement_text, clauses, result, user_id, pdf, current_tenant,
                )
            return code
        except psycopg.errors.UniqueViolation:
            continue
    raise RuntimeError("Could not generate a unique case code")


def _insert_case(conn, code, lead_tenant, co_tenants, property_address, filename, agreement_text, clauses, result, user_id, pdf, current_tenant=None):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO cases (code, property_address, household_mode, hmo_status) VALUES (%s, %s, %s, %s) RETURNING id",
            (code, property_address, result.get("household_mode"), result.get("hmo_status")),
        )
        case_id = cur.fetchone()[0]
        # The logged-in uploader owns the row for their own name: the lead unless they are a listed co-tenant.
        owner = current_tenant.strip().casefold() if current_tenant else lead_tenant.strip().casefold()
        if owner != lead_tenant.strip().casefold() and owner not in {n.strip().casefold() for n in co_tenants}:
            owner = lead_tenant.strip().casefold()
        cur.executemany(
            "INSERT INTO tenants (case_id, user_id, name, role) VALUES (%s, %s, %s, %s)",
            [
                (case_id, user_id if lead_tenant.strip().casefold() == owner else None, lead_tenant, "lead"),
                *(
                    (case_id, user_id if name.strip().casefold() == owner else None, name, "co_tenant")
                    for name in co_tenants
                ),
            ],
        )
        _insert_agreement(cur, case_id, filename, agreement_text, clauses, result, pdf)


def _insert_agreement(cur, case_id, filename, agreement_text, clauses, result, pdf) -> None:
    """The agreement row, its clauses, findings and email draft for one case."""
    cur.execute(
        """INSERT INTO agreements
             (case_id, original_filename, page_count, text, summary, analysis_mode, law_data_available, pdf)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (
            case_id, filename, result["pages"], agreement_text, result["summary"],
            result["mode"], result["law_data_available"], pdf,
        ),
    )
    agreement_id = cur.fetchone()[0]

    # One row per clause number.
    clause_texts: dict[str, str] = {}
    for clause in clauses:
        clause_texts.setdefault(clause["clause_ref"].strip(), clause["text"])
    clause_ids = _add_clauses(cur, agreement_id, clause_texts)
    _insert_findings(cur, agreement_id, clause_ids, result)
    _save_email(cur, case_id, result.get("email", ""))


def _add_clauses(cur, agreement_id: int, clause_texts: dict[str, str]) -> dict[str, int]:
    if not clause_texts:
        return {}
    # Batched: Tiger Data is a round trip away from the server.
    cur.executemany(
        "INSERT INTO clauses (agreement_id, number, text) VALUES (%s, %s, %s) RETURNING id",
        [(agreement_id, number, text) for number, text in clause_texts.items()],
        returning=True,
    )
    return dict(zip(clause_texts, (result_set.fetchone()[0] for result_set in cur.results())))


def _save_email(cur, case_id, body: str) -> None:
    cur.execute("DELETE FROM emails WHERE case_id = %s", (case_id,))
    if body:
        cur.execute("INSERT INTO emails (case_id, body) VALUES (%s, %s)", (case_id, body))


def _insert_findings(cur, agreement_id: int, clause_ids: dict[str, int], result: dict) -> None:
    """Need-to-knows and flags. Findings that name a clause the agreement doesn't have get
    their own clause row with empty text."""
    missing = {
        item["clause_ref"].strip(): ""
        for item in [*result["need_to_knows"], *result["flags"]]
        if item["clause_ref"].strip() not in clause_ids
    }
    clause_ids = {**clause_ids, **_add_clauses(cur, agreement_id, missing)}
    rows = [
        (clause_ids[item["clause_ref"].strip()], "need_to_know", item["topic"], item["text"],
         None, None, None, None)
        for item in result["need_to_knows"]
    ]
    for flag in result["flags"]:
        citation = flag.get("citation") or {}
        rows.append((
            clause_ids[flag["clause_ref"].strip()], flag["kind"], None, flag["explanation"],
            flag["clause_excerpt"], citation.get("source"), citation.get("section"), citation.get("url"),
        ))
    if rows:
        cur.executemany(
            """INSERT INTO flags
                 (clause_id, kind, topic, explanation, excerpt, cited_source, cited_section, cited_url)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            rows,
        )


def get_agreement_source(conn: psycopg.Connection, code: str) -> dict | None:
    """What an amendment needs: the case's tenants, setup, agreement text and clauses."""
    row = conn.execute(
        """SELECT c.id, c.property_address, c.household_mode, c.hmo_status, a.id, a.text, a.page_count
           FROM cases c JOIN agreements a ON a.case_id = c.id WHERE c.code = %s""",
        (code,),
    ).fetchone()
    if row is None:
        return None
    case_id, address, household_mode, hmo_status, agreement_id, text, pages = row
    tenants = conn.execute(
        "SELECT name, role FROM tenants WHERE case_id = %s ORDER BY role = 'co_tenant', id", (case_id,)
    ).fetchall()
    clauses = [
        {"clause_ref": number, "text": clause_text}
        for number, clause_text in conn.execute(
            "SELECT number, text FROM clauses WHERE agreement_id = %s AND text <> '' ORDER BY id",
            (agreement_id,),
        )
    ]
    return {
        "case_id": case_id,
        "agreement_id": agreement_id,
        "property_address": address,
        "household_mode": household_mode or "single",
        "hmo_status": hmo_status or "unknown",
        "lead_tenant": next((name for name, role in tenants if role == "lead"), ""),
        "co_tenants": [name for name, role in tenants if role == "co_tenant"],
        "text": text,
        "pages": pages,
        "clauses": clauses,
    }


def replace_agreement(
    conn: psycopg.Connection,
    code: str,
    *,
    filename: str | None,
    agreement_text: str,
    clauses: list[dict],
    result: dict,
    pdf: bytes,
) -> None:
    """A new PDF version of the agreement: replace the agreement, its clauses and findings
    (the case, its code and tenants stay the same)."""
    with conn.transaction(), conn.cursor() as cur:
        case_id = cur.execute("SELECT id FROM cases WHERE code = %s", (code,)).fetchone()[0]
        cur.execute("DELETE FROM agreements WHERE case_id = %s", (case_id,))  # cascades to clauses, flags
        _insert_agreement(cur, case_id, filename, agreement_text, clauses, result, pdf)
        cur.execute(
            "INSERT INTO agreement_amendments (case_id, kind, body) VALUES (%s, 'pdf', %s)",
            (case_id, filename or "agreement.pdf"),
        )


def amend_agreement(
    conn: psycopg.Connection,
    source: dict,
    *,
    amendment: str,
    changes: list[dict[str, str]],
    agreement_text: str,
    result: dict,
) -> None:
    """A written amendment: update the changed clauses in place, add new ones, and replace the
    findings with the re-analysis of the amended agreement."""
    agreement_id = source["agreement_id"]
    with conn.transaction(), conn.cursor() as cur:
        clause_ids = {
            number: clause_id
            for clause_id, number in cur.execute(
                "SELECT id, number FROM clauses WHERE agreement_id = %s ORDER BY id DESC", (agreement_id,)
            ).fetchall()
        }
        added = {}
        for change in changes:
            if change["clause_ref"] in clause_ids:
                cur.execute(
                    "UPDATE clauses SET text = %s WHERE id = %s",
                    (change["text"], clause_ids[change["clause_ref"]]),
                )
            else:
                added[change["clause_ref"]] = change["text"]
        clause_ids.update(_add_clauses(cur, agreement_id, added))
        cur.execute(
            """UPDATE agreements SET text = %s, summary = %s, analysis_mode = %s, law_data_available = %s
               WHERE id = %s""",
            (agreement_text, result["summary"], result["mode"], result["law_data_available"], agreement_id),
        )
        cur.execute(
            "DELETE FROM flags WHERE clause_id IN (SELECT id FROM clauses WHERE agreement_id = %s)",
            (agreement_id,),
        )
        _insert_findings(cur, agreement_id, clause_ids, result)
        _save_email(cur, source["case_id"], result.get("email", ""))
        cur.execute(
            "INSERT INTO agreement_amendments (case_id, kind, body) VALUES (%s, 'text', %s)",
            (source["case_id"], amendment),
        )


def apply_text_changes(text: str, clauses: list[dict], changes: list[dict[str, str]]) -> str:
    """The full agreement text with amended clauses swapped in and new clauses appended.

    Also sets each change's "text": the clause as stored, starting with its number written the
    way the agreement writes it ("3." or "3)").
    """
    by_ref = {clause["clause_ref"]: clause["text"] for clause in clauses}
    for change in changes:
        old = by_ref.get(change["clause_ref"])
        number = change["clause_ref"]
        if number and old:
            written = re.match(rf"\s*{re.escape(number)}[.)]?", old)
            number = written.group(0).strip() if written else number
        new = change["text"] = f"{number} {change['new_text']}" if number else change["new_text"]
        if old and old in text:
            text = text.replace(old, new, 1)
        else:
            text = f"{text.rstrip()}\n\n{new}"
    return text


def get_case(conn: psycopg.Connection, code: str) -> dict | None:
    """A saved case in the same shape as the /api/analyze response, or None."""
    row = conn.execute(
        """SELECT c.id, c.code, c.property_address, a.id, a.original_filename, a.page_count,
                  a.summary, a.analysis_mode, a.law_data_available, e.body, a.pdf IS NOT NULL,
                  c.household_mode, c.hmo_status
           FROM cases c
           JOIN agreements a ON a.case_id = c.id
           LEFT JOIN emails e ON e.case_id = c.id
           WHERE c.code = %s""",
        (code,),
    ).fetchone()
    if row is None:
        return None
    case_id, code, address, agreement_id, filename, pages, summary, mode, law_data_available, email, has_pdf, household_mode, hmo_status = row

    tenants = [
        {"name": name, "role": role}
        for name, role in conn.execute(
            "SELECT name, role FROM tenants WHERE case_id = %s ORDER BY role = 'co_tenant', id", (case_id,)
        )
    ]
    need_to_knows, flags = [], []
    for kind, topic, explanation, excerpt, source, section, url, number in conn.execute(
        """SELECT f.kind, f.topic, f.explanation, f.excerpt, f.cited_source, f.cited_section, f.cited_url, cl.number
           FROM flags f JOIN clauses cl ON cl.id = f.clause_id
           WHERE cl.agreement_id = %s ORDER BY f.id""",
        (agreement_id,),
    ):
        if kind == "need_to_know":
            need_to_knows.append({"topic": topic or "", "clause_ref": number, "text": explanation})
        else:
            flags.append({
                "kind": kind,
                "clause_ref": number,
                "clause_excerpt": excerpt or "",
                "explanation": explanation,
                "citation": (
                    {"source": source, "section": section, "url": url or ""} if source and section else None
                ),
            })

    amendments = [
        {"kind": kind, "note": body if kind == "pdf" else "", "created_at": created_at.isoformat()}
        for kind, body, created_at in conn.execute(
            "SELECT kind, body, created_at FROM agreement_amendments WHERE case_id = %s ORDER BY id",
            (case_id,),
        )
    ]
    lead = next((t["name"] for t in tenants if t["role"] == "lead"), "")
    return {
        "case_code": code,
        "saved": True,
        "tenant_name": lead,
        "lead_tenant": lead,
        "co_tenants": [t["name"] for t in tenants if t["role"] == "co_tenant"],
        "tenants": tenants,
        "property_address": address,
        "household_mode": household_mode or "single",
        "hmo_status": hmo_status or "unknown",
        "filename": filename,
        "pdf_url": pdf_url(code) if has_pdf else None,
        "pages": pages,
        "summary": summary or "",
        "need_to_knows": need_to_knows,
        "flags": flags,
        "email": email or "",
        "mode": mode or "gemini",
        "law_data_available": bool(law_data_available),
        "amendments": amendments,
        "disclaimer": DISCLAIMER,
    }


def pdf_url(code: str) -> str:
    return f"/api/cases/{code}/agreement.pdf"


def get_pdf(conn: psycopg.Connection, code: str) -> tuple[bytes, str | None] | None:
    """The original agreement PDF and its file name, or None if the case or PDF is missing."""
    row = conn.execute(
        """SELECT a.pdf, a.original_filename FROM agreements a JOIN cases c ON c.id = a.case_id
           WHERE c.code = %s AND a.pdf IS NOT NULL""",
        (code,),
    ).fetchone()
    return (bytes(row[0]), row[1]) if row else None


def list_cases(conn: psycopg.Connection, user_id: str) -> list[dict]:
    """The cases a user is linked to, newest first."""
    return [
        {"case_code": code, "property_address": address, "role": role, "created_at": created_at.isoformat()}
        for code, address, role, created_at in conn.execute(
            """SELECT c.code, c.property_address, t.role, c.created_at
               FROM tenants t JOIN cases c ON c.id = t.case_id
               WHERE t.user_id = %s ORDER BY c.created_at DESC""",
            (user_id,),
        )
    ]


def join_case(conn: psycopg.Connection, code: str, user_id: str, display_name: str) -> bool:
    """Link a user to a case by its code. They take the unclaimed tenant whose name matches
    theirs, otherwise they are added as a new co-tenant. False if no case has that code."""
    with conn.transaction():
        row = conn.execute("SELECT id FROM cases WHERE code = %s", (code,)).fetchone()
        if row is None:
            return False
        case_id = row[0]
        already = conn.execute(
            "SELECT 1 FROM tenants WHERE case_id = %s AND user_id = %s", (case_id, user_id)
        ).fetchone()
        if already:
            return True
        unclaimed = conn.execute(
            """SELECT id FROM tenants
               WHERE case_id = %s AND user_id IS NULL AND lower(name) = lower(%s)
               ORDER BY id LIMIT 1 FOR UPDATE""",
            (case_id, display_name.strip()),
        ).fetchone()
        if unclaimed:
            conn.execute("UPDATE tenants SET user_id = %s WHERE id = %s", (user_id, unclaimed[0]))
        else:
            conn.execute(
                "INSERT INTO tenants (case_id, user_id, name, role) VALUES (%s, %s, %s, 'co_tenant')",
                (case_id, user_id, display_name.strip()),
            )
    return True
