"""Agreement analysis: PDF -> clauses -> retrieve law -> classify -> landlord email."""

import io
import re

from pypdf import PdfReader

from app import config, llm
from app.rag.retrieve import retrieve

PLACEHOLDER_FLAGS = [
    {
        "kind": "clarification",
        "clause": "Guests",
        "message": "Placeholder: confirm how long guests may stay overnight.",
        "citation": "",
    },
    {
        "kind": "legal",
        "clause": "Fees",
        "message": "Placeholder: check for fees banned under the Tenant Fees Act 2019.",
        "citation": "Tenant Fees Act 2019",
    },
]


def extract_text(pdf_bytes: bytes) -> tuple[str, int]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages), len(reader.pages)


# Numbered clauses such as "1.", "4.2" or "12)" at the start of a line.
CLAUSE_START = re.compile(r"^\s*\d+(?:\.\d+)*[.)]?\s+", re.MULTILINE)


def split_clauses(text: str, min_chars: int = 40) -> list[str]:
    starts = [m.start() for m in CLAUSE_START.finditer(text)]
    if not starts:
        parts = re.split(r"\n\s*\n", text)
    else:
        parts = [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]
    return [p.strip() for p in parts if len(p.strip()) >= min_chars]


def analyze(pdf_bytes: bytes, tenant_name: str, property_address: str) -> dict:
    text, pages = extract_text(pdf_bytes)

    if not config.GEMINI_API_KEY:
        return {"pages": pages, "flags": PLACEHOLDER_FLAGS, "email": "", "mode": "placeholder"}

    flags = []
    for clause in split_clauses(text):
        result = llm.classify_clause(clause, retrieve(clause))
        if result["verdict"] == "ok":
            continue
        flags.append(
            {
                "kind": "legal" if result["verdict"] == "contradicts_law" else "clarification",
                "clause": clause[:80],
                "message": result["explanation"],
                "citation": result.get("citation", ""),
            }
        )

    email = llm.draft_landlord_email(tenant_name, property_address, flags) if flags else ""
    return {"pages": pages, "flags": flags, "email": email, "mode": "gemini"}
