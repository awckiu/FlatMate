"""Thin wrapper around the Gemini API (google-genai)."""

import json
from functools import lru_cache

from google import genai
from google.genai import types

from app import config


@lru_cache
def client() -> genai.Client:
    if not config.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set in backend/.env")
    return genai.Client(api_key=config.GEMINI_API_KEY)


def embed(texts: list[str]) -> list[list[float]]:
    result = client().models.embed_content(model=config.GEMINI_EMBED_MODEL, contents=texts)
    return [e.values for e in result.embeddings]


CLASSIFY_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "clause_id": {"type": "integer"},
            "verdict": {"type": "string", "enum": ["ok", "clarify", "contradicts_law"]},
            "explanation": {"type": "string"},
            "citation": {"type": "string"},
        },
        "required": ["clause_id", "verdict", "explanation"],
    },
}


def classify_clauses(clauses: list[str], law_sections: list[dict]) -> list[dict]:
    """Classify many tenancy clauses against retrieved UK law sections in one Gemini call.

    Returns one result per clause the model flagged or cleared, keyed by `clause_id`
    (the clause's index in `clauses`).
    """
    context = "\n\n".join(f"[{s['source']} {s['section']}]\n{s['text']}" for s in law_sections)
    numbered = "\n\n".join(f"<clause id={i}>\n{c}\n</clause>" for i, c in enumerate(clauses))
    prompt = (
        "You help UK tenants understand their tenancy agreement.\n"
        "Using ONLY the law sections below, classify every clause:\n"
        "- ok: nothing to flag\n"
        "- clarify: ambiguous or worth confirming with the landlord\n"
        "- contradicts_law: conflicts with the cited law\n"
        "Return one item per clause with its clause_id. Cite the section you relied on. "
        "If the sections don't cover it, don't claim illegality.\n\n"
        f"LAW SECTIONS:\n{context}\n\nCLAUSES:\n{numbered}"
    )
    response = client().models.generate_content(
        model=config.GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CLASSIFY_SCHEMA,
        ),
    )
    return json.loads(response.text)


def draft_landlord_email(tenant_name: str, property_address: str, flags: list[dict]) -> str:
    issues = "\n".join(f"- {f['clause']}: {f['message']}" for f in flags)
    prompt = (
        "Write a short, polite email from a tenant to their landlord asking about these points "
        f"in the tenancy agreement for {property_address}. Sign it from {tenant_name}.\n\n{issues}"
    )
    return client().models.generate_content(model=config.GEMINI_MODEL, contents=prompt).text
