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


def retrieve_many(queries: list[str], k: int = 4) -> list[list[dict]]:
    """Retrieve relevant law sections for several clauses with batched embeddings."""
    if not queries:
        return []

    store = _store()
    if store.is_empty():
        return [[] for _ in queries]
    results: list[list[dict]] = []
    for start in range(0, len(queries), 100):
        batch = queries[start : start + 100]
        embeddings = llm.embed(batch)
        results.extend(
            [[asdict(chunk) for chunk in store.search(embedding, k)] for embedding in embeddings]
        )
    return results
