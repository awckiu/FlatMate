from dataclasses import asdict
from functools import lru_cache

from app import llm
from app.rag.store import get_store


@lru_cache
def _store():
    return get_store()


def retrieve(query: str, k: int = 4) -> list[dict]:
    """Top-k law sections relevant to a tenancy clause."""
    return retrieve_many([query], k)[0]


# The Gemini embeddings API accepts up to 100 texts per request.
EMBED_BATCH = 100


def retrieve_many(queries: list[str], k: int = 4) -> list[list[dict]]:
    """Top-k law sections for each query, embedding the queries in as few calls as possible."""
    embeddings = []
    for i in range(0, len(queries), EMBED_BATCH):
        embeddings.extend(llm.embed(queries[i : i + EMBED_BATCH]))
    return [[asdict(c) for c in _store().search(e, k)] for e in embeddings]
