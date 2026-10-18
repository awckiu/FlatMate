"""Parse the fetched law files into section-sized chunks and load them into the vector store."""

from dataclasses import dataclass

from app import llm
from app.rag.store import Chunk, VectorStore

MAX_CHARS = 4000


@dataclass
class LawFile:
    source: str
    url: str
    last_updated: str  # YYYY-MM-DD
    chunks: list[Chunk]


def parse_law_file(text: str) -> LawFile:
    """Parse the format scripts/fetch_law.py writes (see backend/data/law/README.md)."""
    header, _, body = text.partition("\n### ")
    meta = {}
    for line in header.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    missing = [key for key in ("source", "url", "last_updated") if not meta.get(key)]
    if missing:
        raise ValueError(f"law file header is missing {', '.join(missing)}")

    chunks = []
    for block in ("### " + body).split("\n### ") if body else []:
        title, _, section_text = block.removeprefix("### ").partition("\n")
        parts = [part.strip() for part in title.split("|")]
        if len(parts) != 3 or not parts[0]:
            raise ValueError(f"bad section line: ### {title}")
        section_ref, heading, url = parts
        for piece in _split(section_text.strip()):
            chunks.append(Chunk(meta["source"], section_ref, piece, url, heading))
    return LawFile(meta["source"], meta["url"], meta["last_updated"], chunks)


def _split(text: str) -> list[str]:
    """Split a long section into pieces of at most MAX_CHARS, at line breaks where possible."""
    pieces, current = [], ""
    for line in text.splitlines():
        if current and len(current) + 1 + len(line) > MAX_CHARS:
            pieces.append(current)
            current = ""
        while len(line) > MAX_CHARS:
            pieces.append(line[:MAX_CHARS])
            line = line[MAX_CHARS:]
        current = f"{current}\n{line}" if current else line
    return pieces + ([current] if current else [])


def ingest(
    store: VectorStore,
    chunks: list[Chunk],
    batch_size: int = 50,
    last_updated: str | None = None,
    source_url: str | None = None,
) -> int:
    if not chunks:
        return 0
    embeddings = []
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        # The heading helps matching ("Access for repairs"), so it is embedded with the text.
        embeddings.extend(llm.embed([f"{c.heading}\n{c.text}" if c.heading else c.text for c in batch]))
    store.replace_source(chunks[0].source, chunks, embeddings, last_updated, source_url)
    return len(chunks)
