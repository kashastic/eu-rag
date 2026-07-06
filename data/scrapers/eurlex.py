"""EUR-Lex source puller: fetch the corpus shortlist, verify, cache, ingest.

Usage:
    python -m data.scrapers.eurlex             # pull + ingest tiers 1 and 2
    python -m data.scrapers.eurlex --tier 1    # limit to one tier
    python -m data.scrapers.eurlex --dry-run   # fetch + verify only, no ingest
    python -m data.scrapers.eurlex --force     # refetch even if cached

Raw HTML is cached under data/raw/eurlex/ (gitignored) so re-chunking never
re-fetches. Every document is verified against expected title phrases before
ingestion; a CELEX that resolves to the wrong law is reported and skipped,
never ingested. Requests are rate-limited and identified with a UA string
(docs/DATA_SOURCES.md rule 3).

EU legal texts are free to reuse with attribution (Decision 2011/833/EU).
"""

import argparse
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from core.ingestion.document_loader import Document, html_to_text, make_document

logger = logging.getLogger(__name__)

EURLEX_URL = "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:{celex}"
RAW_DIR = Path(__file__).resolve().parents[1] / "raw" / "eurlex"
USER_AGENT = (
    "EURAG-corpus-builder/0.1 (citation-first RAG for EU SME compliance; "
    "contact: akash1acharya@gmail.com)"
)
MIN_REQUEST_INTERVAL = 10.0  # eur-lex.europa.eu/robots.txt sets Crawl-delay: 10
VERIFY_WINDOW = 6000  # chars of extracted text searched for verify phrases


class VerificationError(ValueError):
    """The fetched text does not match the law the CELEX was expected to be."""


@dataclass(frozen=True)
class ShortlistEntry:
    celex: str
    title: str  # canonical corpus title; golden tests match on substrings of it
    verify: tuple[str, ...]  # all must appear (case-insensitive) near the top
    tier: int

    @property
    def url(self) -> str:
        return EURLEX_URL.format(celex=self.celex)

    @property
    def cache_path(self) -> Path:
        return RAW_DIR / f"{self.celex}.html"


# docs/CORPUS_SHORTLIST.md tiers 1 and 2. `verify` pairs the act number with a
# distinctive phrase from the official title so a mistyped CELEX cannot
# silently ingest the wrong law.
SHORTLIST: list[ShortlistEntry] = [
    # --- Tier 1 — data & digital compliance --------------------------------
    ShortlistEntry(
        "32016R0679",
        "Regulation (EU) 2016/679 — General Data Protection Regulation (GDPR)",
        ("regulation (eu) 2016/679", "general data protection regulation"),
        tier=1,
    ),
    ShortlistEntry(
        "32002L0058",
        "Directive 2002/58/EC — ePrivacy Directive",
        ("directive 2002/58", "privacy and electronic communications"),
        tier=1,
    ),
    ShortlistEntry(
        "32024R1689",
        "Regulation (EU) 2024/1689 — Artificial Intelligence Act (AI Act)",
        ("regulation (eu) 2024/1689", "artificial intelligence"),
        tier=1,
    ),
    ShortlistEntry(
        "32022R2065",
        "Regulation (EU) 2022/2065 — Digital Services Act (DSA)",
        ("regulation (eu) 2022/2065", "digital services act"),
        tier=1,
    ),
    ShortlistEntry(
        "32022R1925",
        "Regulation (EU) 2022/1925 — Digital Markets Act (DMA)",
        ("regulation (eu) 2022/1925", "digital markets act"),
        tier=1,
    ),
    ShortlistEntry(
        "32022L2555",
        "Directive (EU) 2022/2555 — NIS2 Cybersecurity Directive",
        ("directive (eu) 2022/2555", "cybersecurity"),
        tier=1,
    ),
    ShortlistEntry(
        "32023R2854",
        "Regulation (EU) 2023/2854 — Data Act",
        ("regulation (eu) 2023/2854", "data act"),
        tier=1,
    ),
    ShortlistEntry(
        "32019R1150",
        "Regulation (EU) 2019/1150 — Platform-to-Business Regulation",
        ("regulation (eu) 2019/1150", "online intermediation services"),
        tier=1,
    ),
    ShortlistEntry(
        "32024R2847",
        "Regulation (EU) 2024/2847 — Cyber Resilience Act (CRA)",
        ("regulation (eu) 2024/2847", "products with digital elements"),
        tier=1,
    ),
    ShortlistEntry(
        "32000L0031",
        "Directive 2000/31/EC — e-Commerce Directive",
        ("directive 2000/31/ec", "electronic commerce"),
        tier=1,
    ),
    ShortlistEntry(
        "32019L0790",
        "Directive (EU) 2019/790 — Copyright in the Digital Single Market",
        ("directive (eu) 2019/790", "copyright"),
        tier=1,
    ),
    ShortlistEntry(
        "32016L0943",
        "Directive (EU) 2016/943 — Trade Secrets Directive",
        ("directive (eu) 2016/943", "trade secrets"),
        tier=1,
    ),
    # --- Tier 2 — running an SME -------------------------------------------
    ShortlistEntry(
        "32003H0361",
        "Commission Recommendation 2003/361/EC — SME definition",
        ("2003/361", "micro, small and medium-sized enterprises"),
        tier=2,
    ),
    ShortlistEntry(
        "32011L0007",
        "Directive 2011/7/EU — Late Payment Directive",
        ("directive 2011/7/eu", "late payment"),
        tier=2,
    ),
    ShortlistEntry(
        "32011L0083",
        "Directive 2011/83/EU — Consumer Rights Directive",
        ("directive 2011/83/eu", "consumer rights"),
        tier=2,
    ),
    ShortlistEntry(
        "32023R0988",
        "Regulation (EU) 2023/988 — General Product Safety Regulation",
        ("regulation (eu) 2023/988", "general product safety"),
        tier=2,
    ),
    ShortlistEntry(
        "32006L0112",
        "Council Directive 2006/112/EC — VAT Directive",
        ("directive 2006/112/ec", "value added tax"),
        tier=2,
    ),
    ShortlistEntry(
        "32022L2464",
        "Directive (EU) 2022/2464 — Corporate Sustainability Reporting"
        " Directive (CSRD)",
        ("directive (eu) 2022/2464", "sustainability reporting"),
        tier=2,
    ),
    ShortlistEntry(
        "32019L1937",
        "Directive (EU) 2019/1937 — Whistleblower Protection Directive",
        ("directive (eu) 2019/1937", "report breaches of union law"),
        tier=2,
    ),
    ShortlistEntry(
        "32019L0882",
        "Directive (EU) 2019/882 — European Accessibility Act",
        ("directive (eu) 2019/882", "accessibility requirements"),
        tier=2,
    ),
    ShortlistEntry(
        "32005L0029",
        "Directive 2005/29/EC — Unfair Commercial Practices Directive",
        ("directive 2005/29/ec", "unfair business-to-consumer commercial practices"),
        tier=2,
    ),
    ShortlistEntry(
        "31993L0013",
        "Council Directive 93/13/EEC — Unfair Contract Terms Directive",
        ("93/13/eec", "unfair terms in consumer contracts"),
        tier=2,
    ),
    ShortlistEntry(
        "32019L0771",
        "Directive (EU) 2019/771 — Sale of Goods Directive",
        ("directive (eu) 2019/771", "sale of goods"),
        tier=2,
    ),
    ShortlistEntry(
        "32019L0770",
        "Directive (EU) 2019/770 — Digital Content and Services Directive",
        ("directive (eu) 2019/770", "digital content"),
        tier=2,
    ),
    ShortlistEntry(
        "32018R0302",
        "Regulation (EU) 2018/302 — Geo-blocking Regulation",
        ("regulation (eu) 2018/302", "geo-blocking"),
        tier=2,
    ),
    ShortlistEntry(
        "32024L2853",
        "Directive (EU) 2024/2853 — Product Liability Directive",
        ("directive (eu) 2024/2853", "liability for defective products"),
        tier=2,
    ),
    ShortlistEntry(
        "32006L0123",
        "Directive 2006/123/EC — Services Directive",
        ("directive 2006/123/ec", "services in the internal market"),
        tier=2,
    ),
    ShortlistEntry(
        "32003L0088",
        "Directive 2003/88/EC — Working Time Directive",
        ("directive 2003/88/ec", "organisation of working time"),
        tier=2,
    ),
    ShortlistEntry(
        "32019L1152",
        "Directive (EU) 2019/1152 — Transparent and Predictable Working"
        " Conditions Directive",
        ("directive (eu) 2019/1152", "transparent and predictable working conditions"),
        tier=2,
    ),
    ShortlistEntry(
        "32023L0970",
        "Directive (EU) 2023/970 — Pay Transparency Directive",
        ("directive (eu) 2023/970", "pay transparency"),
        tier=2,
    ),
    ShortlistEntry(
        "32020L0285",
        "Council Directive (EU) 2020/285 — VAT Small Enterprise Scheme",
        ("directive (eu) 2020/285", "special scheme for small enterprises"),
        tier=2,
    ),
]

from data.scrapers.common import ssl_context as _ssl_context  # noqa: E402

_last_request = 0.0


def fetch_html(entry: ShortlistEntry, *, force: bool = False) -> str:
    """Return the document HTML, from cache when possible."""
    if entry.cache_path.is_file() and not force:
        return entry.cache_path.read_text(encoding="utf-8", errors="replace")

    global _last_request
    wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    logger.info("fetching %s (%s)", entry.celex, entry.title)
    request = urllib.request.Request(entry.url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=90, context=_ssl_context()) as response:
        html = response.read().decode("utf-8", errors="replace")
    _last_request = time.monotonic()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    entry.cache_path.write_text(html, encoding="utf-8")
    return html


def document_from_html(entry: ShortlistEntry, html: str) -> Document:
    """Convert to text and verify it is the expected law before building the
    Document. EUR-Lex answers unknown CELEX numbers with an HTTP 200 error
    page, so status codes alone prove nothing."""
    text = html_to_text(html)
    head = text[:VERIFY_WINDOW].lower()
    missing = [phrase for phrase in entry.verify if phrase.lower() not in head]
    if missing:
        raise VerificationError(
            f"{entry.celex}: text does not match '{entry.title}'"
            f" (expected phrases not found: {missing})"
        )
    return make_document(
        title=entry.title,
        text=text,
        source_url=entry.url,
        source_type="eur-lex",
        language="en",
    )


def load_cached_documents(
    entries: list[ShortlistEntry] | None = None,
) -> dict[str, Document]:
    """Verified Documents from the local cache only — never touches the
    network, so the seed script and tests stay offline."""
    docs: dict[str, Document] = {}
    for entry in entries or SHORTLIST:
        if not entry.cache_path.is_file():
            continue
        try:
            docs[entry.celex] = document_from_html(
                entry, entry.cache_path.read_text(encoding="utf-8", errors="replace")
            )
        except (VerificationError, ValueError) as exc:
            logger.warning("skipping cached %s: %s", entry.celex, exc)
    return docs


def pull(
    entries: list[ShortlistEntry],
    *,
    pipeline=None,
    force: bool = False,
) -> list[tuple[ShortlistEntry, str]]:
    """Fetch, verify, and (unless pipeline is None) ingest each entry.
    Returns (entry, status) pairs; failures are reported, never ingested."""
    results = []
    for entry in entries:
        try:
            doc = document_from_html(entry, fetch_html(entry, force=force))
        except VerificationError as exc:
            logger.error("SKIPPED %s", exc)
            results.append((entry, "SKIPPED — title mismatch, not ingested"))
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.error("SKIPPED %s: fetch failed: %s", entry.celex, exc)
            results.append((entry, f"SKIPPED — fetch failed ({exc})"))
            continue
        if pipeline is None:
            results.append((entry, "verified (dry run)"))
        else:
            n_chunks = pipeline.ingest(doc)
            status = f"{n_chunks} chunks" if n_chunks else "unchanged, skipped"
            results.append((entry, status))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tier", type=int, choices=(1, 2), help="limit to one tier")
    parser.add_argument("--force", action="store_true", help="refetch cached HTML")
    parser.add_argument(
        "--dry-run", action="store_true", help="fetch and verify only, no ingest"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    entries = [e for e in SHORTLIST if args.tier in (None, e.tier)]
    if args.dry_run:
        results = pull(entries, force=args.force)
    else:
        from core.pipeline import Pipeline

        pipeline = Pipeline()
        try:
            results = pull(entries, pipeline=pipeline, force=args.force)
        finally:
            pipeline.close()

    print()
    failures = 0
    for entry, status in results:
        failures += status.startswith("SKIPPED")
        print(f"  • [{entry.celex}] {entry.title} — {status}")
    print(f"\n{len(results) - failures}/{len(results)} documents OK.")
    if failures:
        raise SystemExit(f"{failures} document(s) skipped — see log above.")


if __name__ == "__main__":
    main()
