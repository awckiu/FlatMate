"""Agreement analysis: PDF -> clauses -> retrieve law -> classify -> landlord email."""

import io
import re

from pypdf import PdfReader

from app import config, llm
from app.rag.retrieve import retrieve_many

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


CLAUSES_PER_CALL = 80


def _dedupe(sections) -> list[dict]:
    """Law sections retrieved for several clauses, without repeats."""
    seen = {}
    for s in sections:
        seen.setdefault((s["source"], s["section"], s["text"]), s)
    return list(seen.values())


def analyze(pdf_bytes: bytes, tenant_name: str, property_address: str) -> dict:
    text, pages = extract_text(pdf_bytes)

    if not config.GEMINI_API_KEY:
        return {"pages": pages, "flags": PLACEHOLDER_FLAGS, "email": "", "mode": "placeholder"}

    clauses = split_clauses(text)
    retrieved = retrieve_many(clauses)

    flags = []
    # One Gemini call per batch of clauses (usually the whole agreement) instead of one per
    # clause, so a long agreement stays well inside the free tier's per-minute limits.
    for i in range(0, len(clauses), CLAUSES_PER_CALL):
        batch = clauses[i : i + CLAUSES_PER_CALL]
        law = _dedupe(sec for sections in retrieved[i : i + CLAUSES_PER_CALL] for sec in sections)
        for result in llm.classify_clauses(batch, law):
            idx = result.get("clause_id")
            if result["verdict"] == "ok" or not isinstance(idx, int) or not 0 <= idx < len(batch):
                continue
            flags.append(
                {
                    "kind": "legal" if result["verdict"] == "contradicts_law" else "clarification",
                    "clause": batch[idx][:80],
                    "message": result["explanation"],
                    "citation": result.get("citation", ""),
                }
            )

    email = llm.draft_landlord_email(tenant_name, property_address, flags) if flags else ""
    return {"pages": pages, "flags": flags, "email": email, "mode": "gemini"}
