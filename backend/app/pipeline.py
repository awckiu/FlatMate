"""Agreement analysis: PDF -> text -> retrieve law -> batched Gemini analysis -> email."""

import io
import re

from pypdf import PdfReader

from app import config, llm
from app.rag.retrieve import retrieve_many

MAX_ANALYSIS_CHARS = 100_000
CLAUSE_START = re.compile(r"^\s*(\d+(?:\.\d+)*)(?:[.)])?\s+", re.MULTILINE)


class AnalysisInputError(ValueError):
    """The uploaded PDF cannot be analyzed as text within the supported limits."""


def extract_text(pdf_bytes: bytes) -> tuple[str, int]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    if reader.is_encrypted:
        raise AnalysisInputError("Password-protected PDFs are not supported.")
    return "\n".join(page.extract_text() or "" for page in reader.pages), len(reader.pages)


def split_clauses(text: str) -> list[dict[str, str]]:
    matches = list(CLAUSE_START.finditer(text))
    if not matches:
        parts = [(None, part) for part in re.split(r"\n\s*\n", text)]
    else:
        parts = [
            (match.group(1), text[match.start() : next_match.start() if next_match else len(text)])
            for match, next_match in zip(matches, matches[1:] + [None])
        ]

    return [
        {"clause_ref": reference or "", "text": clause.strip()}
        for reference, clause in parts
        if len(clause.strip()) >= 40
    ]


def _validate_analysis(result: dict) -> None:
    if not isinstance(result, dict) or not isinstance(result.get("summary"), str):
        raise RuntimeError("Gemini returned an invalid agreement summary")
    if not isinstance(result.get("need_to_knows"), list) or not isinstance(result.get("flags"), list):
        raise RuntimeError("Gemini returned an invalid analysis structure")
    for item in result["need_to_knows"]:
        if not isinstance(item, dict) or any(
            not isinstance(item.get(field), str) for field in ("topic", "clause_ref", "text")
        ):
            raise RuntimeError("Gemini returned an invalid need-to-know item")
    for flag in result["flags"]:
        if not isinstance(flag, dict) or flag.get("kind") not in {"clarify", "contradicts_law"}:
            raise RuntimeError("Gemini returned an invalid flag")
        if any(
            not isinstance(flag.get(field), str)
            for field in (
                "clause_ref",
                "clause_excerpt",
                "explanation",
                "citation_source",
                "citation_section",
            )
        ):
            raise RuntimeError("Gemini returned an invalid flag")


def analyze(
    pdf_bytes: bytes,
    tenant_name: str,
    property_address: str,
    co_tenants: list[str] | None = None,
    household_mode: str = "single",
    hmo_status: str = "unknown",
) -> dict:
    text, pages = extract_text(pdf_bytes)
    text = text.strip()
    if not text:
        raise AnalysisInputError(
            "No selectable text was found in this PDF. Scanned agreements are not supported yet."
        )
    if len(text) > MAX_ANALYSIS_CHARS:
        raise AnalysisInputError(
            "This agreement is too long to analyze safely in one request. Please use a shorter PDF."
        )

    if not config.GEMINI_API_KEY:
        return {
            "pages": pages,
            "summary": "Gemini is not configured, so this agreement was not analyzed.",
            "need_to_knows": [],
            "flags": [],
            "email": "",
            "mode": "placeholder",
            "law_data_available": False,
            "household_mode": household_mode,
            "hmo_status": hmo_status,
            "disclaimer": "This tool provides information, not legal advice.",
        }

    clauses = split_clauses(text)
    if not clauses:
        clauses = [{"clause_ref": "", "text": text}]
    household_context = (
        f"Household setup: {household_mode.replace('_', ' ')}. "
        f"HMO status: {hmo_status.replace('_', ' ')}. "
        "Use these facts to prioritize relevant tenancy questions and retrieved housing law; "
        "do not assume legal status beyond the supplied facts.\n"
    )
    retrieved_groups = retrieve_many(
        [household_context + clause["text"] for clause in clauses]
    )
    law_by_reference: dict[tuple[str, str], dict] = {}
    for sections in retrieved_groups:
        for section in sections:
            key = (section["source"].strip().casefold(), section["section"].strip().casefold())
            law_by_reference.setdefault(key, section)
    law_sections = list(law_by_reference.values())

    analysis = llm.analyze_agreement(
        text, law_sections, household_mode=household_mode, hmo_status=hmo_status
    )
    _validate_analysis(analysis)
    flags = []
    for flag in analysis["flags"]:
        kind = flag["kind"]
        source = flag.get("citation_source", "").strip()
        section = flag.get("citation_section", "").strip()
        citation = law_by_reference.get((source.casefold(), section.casefold())) if source and section else None
        explanation = flag.get("explanation", "").strip()

        if kind == "contradicts_law" and citation is None:
            kind = "clarify"
            explanation = (
                "The legal concern could not be verified against the supplied law sections. "
                + explanation
            )

        flags.append(
            {
                "kind": kind,
                "clause_ref": flag.get("clause_ref", ""),
                "clause_excerpt": flag.get("clause_excerpt", ""),
                "explanation": explanation,
                "citation": (
                    {
                        "source": citation["source"],
                        "section": citation["section"],
                        "url": citation.get("url", ""),
                    }
                    if citation is not None
                    else None
                ),
            }
        )

    tenants = [tenant_name, *(co_tenants or [])]
    email = llm.draft_landlord_email(", ".join(tenants), property_address, flags) if flags else ""
    return {
        "pages": pages,
        "summary": analysis["summary"],
        "need_to_knows": analysis["need_to_knows"],
        "flags": flags,
        "email": email,
        "mode": "gemini",
        "law_data_available": bool(law_sections),
        "household_mode": household_mode,
        "hmo_status": hmo_status,
        "disclaimer": "This tool provides information, not legal advice.",
    }
