"""Vector stores for law sections: Tiger Data (pgvector) or a local numpy index."""

import json
from dataclasses import asdict, dataclass
from typing import Protocol

import numpy as np

from app import config


@dataclass
class Chunk:
    source: str  # e.g. "Tenant Fees Act 2019"
    section: str  # e.g. "s.1"
    text: str
    url: str = ""


class VectorStore(Protocol):
    def add(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None: ...

    def replace_source(self, source: str, chunks: list[Chunk], embeddings: list[list[float]]) -> None: ...

    def is_empty(self) -> bool: ...

    def search(self, embedding: list[float], k: int) -> list[Chunk]: ...


class LocalStore:
    """In-memory cosine-similarity index persisted to data/law_index.npz."""

    def __init__(self, path=config.LOCAL_INDEX_PATH):
        self.path = path
        self.vectors = np.zeros((0, 0), dtype=np.float32)
        self.chunks: list[Chunk] = []
        if path.exists():
            data = np.load(path, allow_pickle=False)
            self.vectors = data["vectors"]
            self.chunks = [Chunk(**c) for c in json.loads(str(data["chunks"]))]

    def add(self, chunks, embeddings):
        new = np.asarray(embeddings, dtype=np.float32)
        new /= np.linalg.norm(new, axis=1, keepdims=True)
        self.vectors = new if self.vectors.size == 0 else np.vstack([self.vectors, new])
        self.chunks.extend(chunks)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(self.path, vectors=self.vectors, chunks=json.dumps([asdict(c) for c in self.chunks]))

    def replace_source(self, source, chunks, embeddings):
        if any(chunk.source != source for chunk in chunks):
            raise ValueError("All replacement chunks must belong to the requested source")
        keep = [index for index, chunk in enumerate(self.chunks) if chunk.source != source]
        if self.vectors.size:
            self.vectors = self.vectors[keep]
        self.chunks = [self.chunks[index] for index in keep]
        if chunks:
            new = np.asarray(embeddings, dtype=np.float32)
            new /= np.linalg.norm(new, axis=1, keepdims=True)
            self.vectors = new if self.vectors.size == 0 else np.vstack([self.vectors, new])
            self.chunks.extend(chunks)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(self.path, vectors=self.vectors, chunks=json.dumps([asdict(c) for c in self.chunks]))

    def is_empty(self):
        return not self.chunks

    def search(self, embedding, k):
        if not self.chunks:
            return []
        q = np.asarray(embedding, dtype=np.float32)
        scores = self.vectors @ (q / np.linalg.norm(q))
        return [self.chunks[i] for i in np.argsort(-scores)[:k]]


class PgVectorStore:
    """Postgres + pgvector on Tiger Data."""

    def __init__(self, url: str, dim: int = 3072):
        import psycopg
        from pgvector.psycopg import register_vector

        self.conn = psycopg.connect(url, autocommit=True)
        self.conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(self.conn)
        self.conn.execute(
            f"""CREATE TABLE IF NOT EXISTS law_sections (
                id bigserial PRIMARY KEY,
                source text, section text, text text, url text,
                embedding vector({dim}))"""
        )

    def add(self, chunks, embeddings):
        with self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO law_sections (source, section, text, url, embedding) VALUES (%s, %s, %s, %s, %s)",
                [(c.source, c.section, c.text, c.url, np.asarray(e)) for c, e in zip(chunks, embeddings)],
            )

    def replace_source(self, source, chunks, embeddings):
        if any(chunk.source != source for chunk in chunks):
            raise ValueError("All replacement chunks must belong to the requested source")
        with self.conn.transaction():
            self.conn.execute("DELETE FROM law_sections WHERE source = %s", (source,))
            with self.conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO law_sections (source, section, text, url, embedding) VALUES (%s, %s, %s, %s, %s)",
                    [(c.source, c.section, c.text, c.url, np.asarray(e)) for c, e in zip(chunks, embeddings)],
                )

    def is_empty(self):
        return not bool(self.conn.execute("SELECT EXISTS (SELECT 1 FROM law_sections)").fetchone()[0])

    def search(self, embedding, k):
        rows = self.conn.execute(
            "SELECT source, section, text, url FROM law_sections ORDER BY embedding <=> %s LIMIT %s",
            (np.asarray(embedding), k),
        ).fetchall()
        return [Chunk(*r) for r in rows]


def get_store() -> VectorStore:
    if config.DATABASE_URL:
        return PgVectorStore(config.DATABASE_URL)
    return LocalStore()
