"""Seed the local corpus: bundled samples plus cached EUR-Lex full texts.

Usage:
    python -m data.seed                            # ingest whatever is cached
    python -m data.seed --scrape                   # fill missing caches first (network)
    python -m data.seed --scrape --expect-docs 47  # deploy mode: fail unless full corpus

Idempotent: unchanged documents are skipped via content hash. A file lock on
data/raw/.seed.lock serializes concurrent invocations (e.g. several API
replicas cold-starting at once), so only one process scrapes and embeds.

If `python -m data.scrapers.eurlex` has been run, the real official texts in
data/raw/eurlex/ are ingested and the hand-written excerpts they supersede are
left out; on a fresh clone without the cache, the samples keep working alone.
"""

import argparse
import fcntl
import logging
import urllib.error
from pathlib import Path

from core.ingestion.document_loader import load_sample_file
from core.pipeline import Pipeline
from data.scrapers import eurlex, funding_calls, portals
from data.scrapers.common import RAW_ROOT
from data.scrapers.eurlex import load_cached_documents

SAMPLES_DIR = Path(__file__).resolve().parent / "samples"

# hand-written excerpt -> CELEX of the official text that replaces it
SUPERSEDED_BY_EURLEX = {
    "gdpr_key_articles.txt": "32016R0679",
    "eu_sme_definition.txt": "32003H0361",
}


def seed(pipeline: Pipeline) -> list[tuple[str, int]]:
    results: list[tuple[str, int]] = []
    eurlex_docs = load_cached_documents()
    for path in sorted(SAMPLES_DIR.glob("*.txt")):
        if SUPERSEDED_BY_EURLEX.get(path.name) in eurlex_docs:
            continue
        doc = load_sample_file(path)
        results.append((doc.title, pipeline.ingest(doc)))
    for celex in sorted(eurlex_docs):
        doc = eurlex_docs[celex]
        results.append((doc.title, pipeline.ingest(doc)))
    portal_docs = portals.load_cached_documents()
    for key in sorted(portal_docs):
        doc = portal_docs[key]
        results.append((doc.title, pipeline.ingest(doc)))
    calls_doc = funding_calls.load_cached_document()
    if calls_doc is not None:
        results.append((calls_doc.title, pipeline.ingest(calls_doc)))
    return results


def fill_caches() -> list[str]:
    """Fetch whatever is missing from data/raw so a subsequent seed() sees the
    full corpus. Network access required; cached files are never refetched
    (the scrapers' own throttles and robots handling apply). Returns a list of
    human-readable failures — the caller decides whether they are fatal."""
    failures: list[str] = []
    # pipeline=None = fetch + verify into the cache, no ingest
    for entry, status in eurlex.pull(eurlex.SHORTLIST):
        if status.startswith("SKIPPED"):
            failures.append(f"eurlex {entry.celex}: {status}")
    # the canonical corpus includes every registry entry (EC portals + the 10
    # national agencies); PoliteFetcher enforces robots.txt per host
    for entry, status in portals.pull(list(portals.REGISTRY)):
        if status.startswith("SKIPPED"):
            failures.append(f"portal {entry.key}: {status}")
    try:
        funding_calls.fetch_calls()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        failures.append(f"funding-calls: {exc}")
    return failures


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--scrape",
        action="store_true",
        help="fetch any missing corpus caches into data/raw first (network)",
    )
    parser.add_argument(
        "--expect-docs",
        type=int,
        default=None,
        metavar="N",
        help="exit non-zero unless the registry holds at least N documents after seeding",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    RAW_ROOT.mkdir(parents=True, exist_ok=True)
    lock_file = (RAW_ROOT / ".seed.lock").open("w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX)

        scrape_failures = fill_caches() if args.scrape else []
        for failure in scrape_failures:
            logging.warning("scrape failure: %s", failure)

        pipeline = Pipeline()
        try:
            results = seed(pipeline)
            total_docs = len(pipeline.registry.list_documents())
        finally:
            pipeline.close()
    finally:
        lock_file.close()

    print()
    for title, n_chunks in results:
        status = f"{n_chunks} chunks" if n_chunks else "unchanged, skipped"
        print(f"  • {title} — {status}")
    print(f"\nSeeded {len(results)} documents ({total_docs} in registry).")

    if args.expect_docs is not None and total_docs < args.expect_docs:
        raise SystemExit(
            f"expected at least {args.expect_docs} documents but the registry holds "
            f"{total_docs} — corpus incomplete"
            + (f"; scrape failures: {scrape_failures}" if scrape_failures else "")
        )
    print("Run: uvicorn api.main:app --reload")


if __name__ == "__main__":
    main()
