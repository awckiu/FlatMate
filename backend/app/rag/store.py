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

    def search(self, embedding: list[float], k: int) -> list[Chunk]: ...

    def clear_source(self, source: str) -> None: ...


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
        self._save()

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(self.path, vectors=self.vectors, chunks=json.dumps([asdict(c) for c in self.chunks]))

    def clear_source(self, source):
        keep = [i for i, c in enumerate(self.chunks) if c.source != source]
        self.chunks = [self.chunks[i] for i in keep]
        if self.vectors.size:
            self.vectors = self.vectors[keep]
        self._save()

    def search(self, embedding, k):
        if not self.chunks:
            return []
        q = np.asarray(embedding, dtype=np.float32)
        scores = self.vectors @ (q / np.linalg.norm(q))
        return [self.chunks[i] for i in np.argsort(-scores)[:k]]


class PgVectorStore:
    """Postgres + pgvector on Tiger Data. Tables come from db/schema.sql."""

    def __init__(self, url: str):
        import psycopg
        from pgvector.psycopg import register_vector

        self.conn = psycopg.connect(url, autocommit=True)
        self.conn.execute(config.SCHEMA_PATH.read_text(encoding="utf-8"))
        register_vector(self.conn)

    def add(self, chunks, embeddings):
        with self.conn.cursor() as cur:
            source_ids = {}
            for c in chunks:
                if c.source not in source_ids:
                    cur.execute(
                        """INSERT INTO law_sources (name, url) VALUES (%s, %s)
                           ON CONFLICT (name) DO UPDATE SET url = EXCLUDED.url RETURNING id""",
                        (c.source, c.url),
                    )
                    source_ids[c.source] = cur.fetchone()[0]
            cur.executemany(
                "INSERT INTO law_sections (source_id, section_ref, text, url, embedding) VALUES (%s, %s, %s, %s, %s)",
                [
                    (source_ids[c.source], c.section, c.text, c.url, np.asarray(e))
                    for c, e in zip(chunks, embeddings)
                ],
            )

    def clear_source(self, source):
        self.conn.execute("DELETE FROM law_sources WHERE name = %s", (source,))

    def search(self, embedding, k):
        rows = self.conn.execute(
            """SELECT src.name, s.section_ref, s.text, s.url
               FROM law_sections s JOIN law_sources src ON src.id = s.source_id
               ORDER BY s.embedding <=> %s LIMIT %s""",
            (np.asarray(embedding), k),
        ).fetchall()
        return [Chunk(*r) for r in rows]


def get_store() -> VectorStore:
    if config.DATABASE_URL:
        return PgVectorStore(config.DATABASE_URL)
    return LocalStore()
