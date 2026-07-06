"""Seed the local corpus: bundled samples plus cached EUR-Lex full texts.

Usage: python -m data.seed
Idempotent: unchanged documents are skipped via content hash.

If `python -m data.scrapers.eurlex` has been run, the real official texts in
data/raw/eurlex/ are ingested and the hand-written excerpts they supersede are
left out; on a fresh clone without the cache, the samples keep working alone.
"""

import logging
from pathlib import Path

from core.ingestion.document_loader import load_sample_file
from core.pipeline import Pipeline
from data.scrapers import funding_calls, portals
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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    pipeline = Pipeline()
    try:
        results = seed(pipeline)
    finally:
        pipeline.close()
    print()
    for title, n_chunks in results:
        status = f"{n_chunks} chunks" if n_chunks else "unchanged, skipped"
        print(f"  • {title} — {status}")
    print(f"\nSeeded {len(results)} documents. Run: uvicorn api.main:app --reload")


if __name__ == "__main__":
    main()
