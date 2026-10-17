from dataclasses import asdict
from functools import lru_cache

from app import llm
from app.rag.store import get_store


@lru_cache
def _store():
    return get_store()


def retrieve(query: str, k: int = 4) -> list[dict]:
    """Top-k law sections relevant to a tenancy clause."""
    [embedding] = llm.embed([query])
    return [asdict(c) for c in _store().search(embedding, k)]
