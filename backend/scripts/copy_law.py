"""Copy the law knowledge base (law_sources + law_sections, embeddings included) from the
database in DATABASE_URL to another database, so the main DB needn't re-embed with Gemini.

The target URL is read from the environment variable named by --to (default
MAIN_DATABASE_URL, from backend/.env). Neither URL is ever printed. Only sources listed in
app/rag/sources.py are copied; each one is replaced in the target in its own transaction,
so re-running gives no duplicates. Run from backend/:

    .venv\\Scripts\\python -m scripts.copy_law --dry-run    # show what would be copied
    .venv\\Scripts\\python -m scripts.copy_law              # copy
    .venv\\Scripts\\python -m scripts.copy_law --prune      # also delete untrusted sources in the target
"""

import argparse
import os
import sys
from urllib.parse import urlparse

from app import config
from app.rag.sources import TRUSTED_SOURCES

TRUSTED_NAMES = [s.name for s in TRUSTED_SOURCES]


def same_database(a: str, b: str) -> bool:
    """True if both URLs point at the same host, port and database (credentials ignored)."""
    if a.strip() == b.strip():
        return True
    pa, pb = urlparse(a.strip()), urlparse(b.strip())
    return (pa.hostname, pa.port or 5432, pa.path.strip("/")) == (pb.hostname, pb.port or 5432, pb.path.strip("/"))


def connect(url: str):
    import psycopg

    return psycopg.connect(url, autocommit=True, connect_timeout=20)


def has_law_tables(conn) -> bool:
    return conn.execute("SELECT to_regclass('law_sources') IS NOT NULL").fetchone()[0]


def read_source(conn, name: str):
    """(url, last_updated, rows) for one source, or None if it isn't in this database."""
    found = conn.execute("SELECT id, url, last_updated FROM law_sources WHERE name = %s", (name,)).fetchone()
    if not found:
        return None
    source_id, url, last_updated = found
    rows = conn.execute(
        """SELECT section_ref, heading, text, url, embedding::text
           FROM law_sections WHERE source_id = %s ORDER BY id""",
        (source_id,),
    ).fetchall()
    return url, last_updated, rows


def write_source(conn, name: str, url, last_updated, rows) -> None:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO law_sources (name, url, last_updated) VALUES (%s, %s, %s)
                   ON CONFLICT (name) DO UPDATE SET url = EXCLUDED.url,
                     last_updated = EXCLUDED.last_updated, ingested_at = now()
                   RETURNING id""",
                (name, url, last_updated),
            )
            source_id = cur.fetchone()[0]
            cur.execute("DELETE FROM law_sections WHERE source_id = %s", (source_id,))
            cur.executemany(
                """INSERT INTO law_sections (source_id, section_ref, heading, text, url, embedding)
                   VALUES (%s, %s, %s, %s, %s, %s::vector)""",
                [(source_id, *row) for row in rows],
            )


def untrusted_sources(conn) -> list[str]:
    rows = conn.execute("SELECT name FROM law_sources WHERE NOT (name = ANY(%s)) ORDER BY name", (TRUSTED_NAMES,))
    return [name for (name,) in rows.fetchall()]


def totals(conn) -> tuple[int, int]:
    return conn.execute("SELECT (SELECT count(*) FROM law_sources), (SELECT count(*) FROM law_sections)").fetchone()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--to", default="MAIN_DATABASE_URL", help="env var holding the target database URL")
    parser.add_argument("--prune", action="store_true", help="delete target sources not in sources.py")
    parser.add_argument("--dry-run", action="store_true", help="only print what would be copied")
    args = parser.parse_args(argv)

    source_url, target_url = config.DATABASE_URL, os.getenv(args.to)
    if not source_url:
        sys.exit("DATABASE_URL is not set in backend/.env")
    if not target_url:
        sys.exit(f"{args.to} is not set in backend/.env")
    if same_database(source_url, target_url):
        sys.exit(f"refusing to copy: DATABASE_URL and {args.to} point at the same database")

    with connect(source_url) as src, connect(target_url) as dst:
        if not args.dry_run:
            dst.execute(config.SCHEMA_PATH.read_text(encoding="utf-8"))
        target_ready = has_law_tables(dst)

        for name in TRUSTED_NAMES:
            found = read_source(src, name)
            if not found:
                print(f"skip {name}: not in the source database")
                continue
            url, last_updated, rows = found
            if args.dry_run:
                print(f"would copy {name} (updated {last_updated}): {len(rows)} rows")
            else:
                write_source(dst, name, url, last_updated, rows)
                print(f"copied {name} (updated {last_updated}): {len(rows)} rows")

        stale = untrusted_sources(dst) if target_ready else []
        for name in stale:
            if args.dry_run or not args.prune:
                print(f"{'would delete' if args.prune else 'not in sources.py (use --prune to delete)'}: {name}")
            else:
                dst.execute("DELETE FROM law_sources WHERE name = %s", (name,))
                print(f"deleted {name}")

        if target_ready:
            sources, sections = totals(dst)
            print(f"target now has {sources} sources, {sections} law_sections rows" + (" (unchanged, dry run)" if args.dry_run else ""))
        else:
            print("target has no law tables yet; they will be created on a real run")


if __name__ == "__main__":
    main()
