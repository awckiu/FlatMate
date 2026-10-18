"""Load the trusted UK housing law sources into the vector store.

First download them with scripts.fetch_law (writes backend/data/law/<stem>.txt), then
run from backend/:

    .venv\\Scripts\\python -m scripts.ingest_law
    .venv\\Scripts\\python -m scripts.ingest_law --only how_to_rent

Only files for the sources in app/rag/sources.py are read, and a file is refused if its
header or any section link doesn't match that list. Uses Tiger Data when DATABASE_URL is
set, otherwise the local index.
"""

import argparse
import sys

from app import config
from app.rag.ingest import ingest, parse_law_file
from app.rag.sources import TRUSTED_SOURCES, TrustedSource, is_trusted_url
from app.rag.store import get_store


def load(source: TrustedSource):
    path = config.LAW_TEXT_DIR / f"{source.stem}.txt"
    if not path.is_file():
        print(f"skip {source.name}: {path} not found (run scripts.fetch_law first)")
        return None
    law = parse_law_file(path.read_text(encoding="utf-8"))
    if law.source != source.name or law.url != source.url:
        raise ValueError(f"{path.name}: header says {law.source!r} at {law.url}, expected {source.name!r} at {source.url}")
    untrusted = [c.url for c in law.chunks if not is_trusted_url(c.url)]
    if untrusted:
        raise ValueError(f"{path.name}: section links outside the trusted sites: {untrusted[:3]}")
    if not law.chunks:
        raise ValueError(f"{path.name}: no sections")
    return law


def main():
    parser = argparse.ArgumentParser(description="Embed and store the trusted law sources.")
    parser.add_argument("--only", help="ingest one source by its stem")
    args = parser.parse_args()
    sources = [s for s in TRUSTED_SOURCES if not args.only or s.stem == args.only]
    if not sources:
        sys.exit(f"unknown source {args.only!r}; choose from {', '.join(s.stem for s in TRUSTED_SOURCES)}")

    laws = [law for law in map(load, sources) if law]
    if not laws:
        raise FileNotFoundError(f"No law source files found in {config.LAW_TEXT_DIR}; run scripts.fetch_law first.")

    store = get_store()
    total = 0
    for law in laws:
        total += ingest(store, law.chunks, last_updated=law.last_updated, source_url=law.url)
        print(f"{law.source} (updated {law.last_updated}): {len(law.chunks)} chunks")
    print(f"done, {total} chunks ingested into {'Tiger Data' if config.DATABASE_URL else 'the local index'}")


if __name__ == "__main__":
    main()
