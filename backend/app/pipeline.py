"""Agreement analysis: PDF -> text -> retrieve law -> batched Gemini analysis -> email."""

import io
import logging
import re

from pypdf import PdfReader

from app import config, llm
from app.rag.retrieve import retrieve_many

logger = logging.getLogger(__name__)
MAX_ANALYSIS_CHARS = 100_000
DISCLAIMER = "This tool provides information, not legal advice."
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


_SECTION_WORDS = re.compile(r"\b(sections?|sect|ss?|regulations?|regs?|paragraph|para|schedule|sch)\b\.?", re.I)
_NON_WORD = re.compile(r"[^a-z0-9]+")
# Cosine similarity a flag needs with a supplied law section to count as citing it when
# the model's citation label doesn't line up with any supplied section.
SEMANTIC_MATCH_MIN = 0.6


def _source_key(source: str) -> set[str]:
    words = _NON_WORD.sub(" ", source.casefold().replace("(gov.uk)", "")).split()
    return {word for word in words if word not in {"the", "of", "and", "act", "gov", "uk"}}


def _section_key(section: str) -> str:
    """'s.21', 'Section 21(1)(a)' and '21' all become '21'; 'reg 3' becomes '3'; a heading stays words."""
    cleaned = _SECTION_WORDS.sub(" ", section.casefold())
    number = re.search(r"\d+[a-z]{0,2}", cleaned)
    return number.group(0) if number else " ".join(_NON_WORD.sub(" ", cleaned).split())


def _same_source(cited: str, supplied: str) -> bool:
    a, b = _source_key(cited), _source_key(supplied)
    if not a or not b:
        return False
    return a <= b or b <= a or len(a & b) / len(a | b) >= 0.6


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
    return dot / norm if norm else 0.0


def match_citations(flags: list[dict], law_sections: list[dict]) -> dict[int, dict]:
    """Map each legal flag (by id()) to the supplied law section it cites.

    The model's labels rarely match ours character for character ("Section 21" vs "s.21",
    "Tenant Fees Act" vs "Tenant Fees Act 2019"), so match in three steps: exact label,
    then normalized source and section number, then meaning (embeddings of the flag vs the
    supplied sections, one batched call). Only supplied sections can ever be cited.
    """
    matched: dict[int, dict] = {}
    if not law_sections:
        return matched
    exact = {
        (section["source"].strip().casefold(), section["section"].strip().casefold()): section
        for section in law_sections
    }
    unmatched = []
    for flag in flags:
        source = flag.get("citation_source", "").strip()
        section = flag.get("citation_section", "").strip()
        citation = exact.get((source.casefold(), section.casefold()))
        if citation is None and section:
            wanted = _section_key(section)
            citation = next(
                (
                    candidate
                    for candidate in law_sections
                    if _section_key(candidate["section"]) == wanted
                    and (not source or _same_source(source, candidate["source"]))
                ),
                None,
            )
        if citation is None:
            unmatched.append(flag)
        else:
            matched[id(flag)] = citation

    if unmatched:
        try:
            texts = [
                f"{flag.get('clause_excerpt', '')}\n{flag.get('explanation', '')}" for flag in unmatched
            ] + [
                f"{section['source']} {section['section']} {section.get('heading', '')}\n{section['text'][:2000]}"
                for section in law_sections
            ]
            vectors = llm.embed(texts)
            flag_vectors, law_vectors = vectors[: len(unmatched)], vectors[len(unmatched) :]
            for flag, vector in zip(unmatched, flag_vectors):
                source = flag.get("citation_source", "").strip()
                candidates = [
                    (index, section)
                    for index, section in enumerate(law_sections)
                    if not source or _same_source(source, section["source"])
                ] or list(enumerate(law_sections))
                score, best = max(
                    ((_cosine(vector, law_vectors[index]), section) for index, section in candidates),
                    key=lambda pair: pair[0],
                )
                if score >= SEMANTIC_MATCH_MIN:
                    matched[id(flag)] = best
        except Exception:  # matching by meaning is a bonus; never fail the analysis over it
            logger.exception("Semantic citation matching failed")
    return matched


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
    return analyze_text(text, pages, tenant_name, property_address, co_tenants, household_mode, hmo_status)


def analyze_text(
    text: str,
    pages: int | None,
    tenant_name: str,
    property_address: str,
    co_tenants: list[str] | None = None,
    household_mode: str = "single",
    hmo_status: str = "unknown",
) -> dict:
    """Analyze agreement text: used for a fresh upload and again after an amendment."""
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
            "disclaimer": DISCLAIMER,
            "agreement_text": text,
            "clauses": split_clauses(text),
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
    legal_flags = [flag for flag in analysis["flags"] if flag["kind"] == "contradicts_law"]
    citations = match_citations(legal_flags, law_sections)
    flags = []
    for flag in analysis["flags"]:
        kind = flag["kind"]
        citation = citations.get(id(flag))
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
        "disclaimer": DISCLAIMER,
        # Used to save the case; main.py removes these before responding.
        "agreement_text": text,
        "clauses": clauses,
    }
