"""Split legislation text into section-sized chunks and load them into the vector store."""

import re

from app import llm
from app.rag.store import Chunk, VectorStore

# Lines like "1 Prohibitions applying to landlords" or "21 Recovery of possession..."
SECTION_HEADING = re.compile(r"^\s*(\d+[A-Z]?)\s+[A-Z][^\n]{3,}$", re.MULTILINE)

MAX_CHARS = 4000


def chunk_by_section(text: str, source: str, url: str = "") -> list[Chunk]:
    matches = list(SECTION_HEADING.finditer(text))
    if not matches:
        return [Chunk(source, "", text[i : i + MAX_CHARS], url) for i in range(0, len(text), MAX_CHARS)]

    chunks = []
    for m, nxt in zip(matches, matches[1:] + [None]):
        body = text[m.start() : nxt.start() if nxt else len(text)].strip()
        for i in range(0, len(body), MAX_CHARS):
            chunks.append(Chunk(source, f"s.{m.group(1)}", body[i : i + MAX_CHARS], url))
    return chunks


def ingest(store: VectorStore, chunks: list[Chunk], batch_size: int = 50) -> int:
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        store.add(batch, llm.embed([c.text for c in batch]))
    return len(chunks)
