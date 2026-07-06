"""Open EU funding calls from the Funding & Tenders portal (SEDIA API).

Usage:
    python -m data.scrapers.funding_calls            # fetch + ingest snapshot
    python -m data.scrapers.funding_calls --dry-run

Ingests ONE compact snapshot document listing currently open / forthcoming
grant calls relevant to SMEs (title, identifier, deadline, portal link).
The document title and URL are stable, so re-running replaces the previous
snapshot in place (content hash changes, doc_id doesn't). This is
deliberately time-sensitive data: the snapshot date and a verify-at-source
note are embedded in the text, and the M5 agentic layer will replace this
with live lookups.
"""

import argparse
import json
import logging
import urllib.error
from datetime import date

from core.ingestion.document_loader import Document, make_document
from data.scrapers.common import RAW_ROOT, USER_AGENT, ssl_context

logger = logging.getLogger(__name__)

CACHE_PATH = RAW_ROOT / "funding" / "calls.json"
API_URL = (
    "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
    "?apiKey=SEDIA&text=SME&pageSize=40&pageNumber=1"
)
PORTAL_URL = (
    "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/"
    "opportunities/calls-for-proposals"
)
TOPIC_URL = (
    "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/"
    "opportunities/topic-details/{identifier}"
)
# SEDIA facet ids: type 1 = grants; status forthcoming / open
_QUERY = {
    "bool": {
        "must": [
            {"terms": {"type": ["1"]}},
            {"terms": {"status": ["31094501", "31094502"]}},
        ]
    }
}
DOC_TITLE = "Open EU funding calls relevant to SMEs (Funding & Tenders portal)"


def fetch_calls(*, force: bool = False) -> dict:
    if CACHE_PATH.is_file() and not force:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))

    import urllib.request
    import uuid

    boundary = uuid.uuid4().hex
    parts = []
    for name, payload in (("query", _QUERY), ("languages", ["en"])):
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n'
            "Content-Type: application/json\r\n\r\n"
            f"{json.dumps(payload)}\r\n"
        )
    body = ("".join(parts) + f"--{boundary}--\r\n").encode()
    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    logger.info("querying Funding & Tenders search API")
    with urllib.request.urlopen(request, timeout=60, context=ssl_context()) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def document_from_calls(data: dict) -> Document:
    lines = [
        f"Snapshot of open and forthcoming EU grant calls relevant to SMEs, "
        f"taken {date.today().isoformat()} from the EU Funding & Tenders "
        f"portal ({data.get('totalResults', '?')} open grant topics matched "
        '"SME"; the 40 most relevant are listed). Deadlines change — always '
        f"verify at the portal: {PORTAL_URL}",
        "",
    ]
    count = 0
    for result in data.get("results", []):
        md = result.get("metadata", {})

        def first(key):
            value = md.get(key)
            return value[0] if isinstance(value, list) and value else None

        title, identifier = first("title"), first("identifier")
        if not title or not identifier:
            continue
        deadline = (first("deadlineDate") or "")[:10] or "see portal"
        status = "forthcoming" if first("status") == "31094501" else "open"
        lines.append(
            f"Call topic: {title}\n"
            f"Identifier: {identifier} ({status}, deadline {deadline})\n"
            f"Details: {TOPIC_URL.format(identifier=identifier)}"
        )
        lines.append("")
        count += 1
    if count < 5:
        raise ValueError(f"only {count} usable calls in API response — not ingesting")
    return make_document(
        title=DOC_TITLE,
        text="\n".join(lines),
        source_url=PORTAL_URL,
        source_type="funding-calls",
    )


def load_cached_document() -> Document | None:
    """Snapshot from cache only — no network (seed script, tests)."""
    if not CACHE_PATH.is_file():
        return None
    try:
        return document_from_calls(json.loads(CACHE_PATH.read_text(encoding="utf-8")))
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("skipping cached funding calls: %s", exc)
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="refetch the snapshot")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    try:
        doc = document_from_calls(fetch_calls(force=args.force))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise SystemExit(f"funding calls unavailable: {exc}")

    print(f"\n  • {doc.title}\n    {len(doc.text.split())} words, source {doc.source_url}")
    if not args.dry_run:
        from core.pipeline import Pipeline

        pipeline = Pipeline()
        try:
            n = pipeline.ingest(doc)
        finally:
            pipeline.close()
        print(f"    ingested: {n if n else 'unchanged, skipped'} chunks")


if __name__ == "__main__":
    main()
