"""Load UK housing legislation into the vector store.

Save each source as plain text in backend/data/law/<name>.txt (copied from
legislation.gov.uk / gov.uk), then run from backend/:

    .venv\\Scripts\\python -m scripts.ingest_law

Uses Tiger Data when DATABASE_URL is set, otherwise the local index.
"""

from app import config
from app.rag.ingest import chunk_by_section, ingest
from app.rag.store import get_store

SOURCES = {
    "housing_act_1988": ("Housing Act 1988", "https://www.legislation.gov.uk/ukpga/1988/50"),
    "housing_act_2004": ("Housing Act 2004", "https://www.legislation.gov.uk/ukpga/2004/34"),
    "tenant_fees_act_2019": ("Tenant Fees Act 2019", "https://www.legislation.gov.uk/ukpga/2019/4"),
    "renters_rights_act_2025": ("Renters' Rights Act 2025", "https://www.legislation.gov.uk/ukpga/2025/26"),
    "deposit_protection": (
        "Tenancy deposit protection",
        "https://www.gov.uk/tenancy-deposit-protection",
    ),
    "how_to_rent": ("How to rent (gov.uk)", "https://www.gov.uk/government/publications/how-to-rent"),
}


def main():
    available = []
    for stem, (source, url) in SOURCES.items():
        path = config.LAW_TEXT_DIR / f"{stem}.txt"
        if not path.is_file():
            print(f"skip {source}: {path} not found")
            continue
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            print(f"skip {source}: {path} is empty")
            continue
        available.append((source, url, text))
    if not available:
        raise FileNotFoundError(
            f"No non-empty law source files found in {config.LAW_TEXT_DIR}; "
            "add at least one source before running ingestion."
        )

    store = get_store()
    total = 0
    for source, url, text in available:
        chunks = chunk_by_section(text, source, url)
        total += ingest(store, chunks)
        print(f"{source}: {len(chunks)} chunks")
    print(f"done, {total} chunks ingested")


if __name__ == "__main__":
    main()
