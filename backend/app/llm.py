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
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["ok", "clarify", "contradicts_law"]},
        "explanation": {"type": "string"},
        "citation": {"type": "string"},
    },
    "required": ["verdict", "explanation"],
}


def classify_clause(clause: str, law_sections: list[dict]) -> dict:
    """Classify one tenancy clause against retrieved UK law sections."""
    context = "\n\n".join(f"[{s['source']} {s['section']}]\n{s['text']}" for s in law_sections)
    prompt = (
        "You help UK tenants understand their tenancy agreement.\n"
        "Using ONLY the law sections below, classify the clause:\n"
        "- ok: nothing to flag\n"
        "- clarify: ambiguous or worth confirming with the landlord\n"
        "- contradicts_law: conflicts with the cited law\n"
        "Cite the section you relied on. If the sections don't cover it, don't claim illegality.\n\n"
        f"LAW SECTIONS:\n{context}\n\nCLAUSE:\n{clause}"
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
