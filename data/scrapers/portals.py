"""EC SME portal + national funding-agency pages (Tier 3).

Usage:
    python -m data.scrapers.portals               # EU-official pages only
    python -m data.scrapers.portals --country de nl at   # + national agencies
    python -m data.scrapers.portals --all         # every reachable entry
    python -m data.scrapers.portals --dry-run     # fetch + verify, no ingest

Policy (docs/DATA_SOURCES.md):
- EU-official pages (source_type "ec-portal"): full text, reuse permitted
  with attribution (Decision 2011/833/EU). Pulled by default.
- National agency pages (source_type "national-scheme"): DISABLED by default
  (standing rule) — enable per country. Only an excerpt is stored (~1,200
  words) with the source linked out; robots.txt is enforced by the fetcher.
- Every page is verified against expected phrases before ingestion, and
  pages that extract to almost no text (JS-rendered shells) are skipped.

Known-blocked (2026-07-06, HTTP 403 for our UA): Bpifrance (FR), VLAIO (BE),
een.ec.europa.eu — not in the registry; the curated samples still cover the
headline Bpifrance/EEN facts.
"""

import argparse
import logging
import urllib.error
from dataclasses import dataclass

from core.ingestion.document_loader import Document, html_to_text, make_document
from data.scrapers.common import RAW_ROOT, PoliteFetcher, RobotsDisallowed

logger = logging.getLogger(__name__)

CACHE_DIR = RAW_ROOT / "portals"
MIN_TEXT_WORDS = 100  # true JS shells extract to a few dozen words; official
# hub pages (EC access-to-finance, Almi) are thin but real at ~120 words
EXCERPT_WORDS = 1200  # national pages: excerpt + link out, never full mirror


@dataclass(frozen=True)
class PortalEntry:
    key: str  # cache filename stem
    title: str  # canonical corpus title
    url: str
    verify: tuple[str, ...]  # all must appear (case-insensitive) in the text
    source_type: str  # "ec-portal" | "national-scheme"
    language: str = "en"
    country: str = ""  # ISO code for national entries; "" = EU-official


REGISTRY: list[PortalEntry] = [
    # --- EU-official (pulled by default) ------------------------------------
    PortalEntry(
        "ec_access_finance",
        "EU funding — access to finance for SMEs (EC portal)",
        "https://single-market-economy.ec.europa.eu/access-finance_en",
        ("access to finance",),
        "ec-portal",
    ),
    PortalEntry(
        "ec_sme_strategy",
        "EU SME strategy (EC portal)",
        "https://single-market-economy.ec.europa.eu/smes/sme-strategy_en",
        ("sme strategy",),
        "ec-portal",
    ),
    PortalEntry(
        "ec_smes_overview",
        "SMEs — EU policy overview (EC portal)",
        "https://single-market-economy.ec.europa.eu/smes_en",
        ("small and medium",),
        "ec-portal",
    ),
    # --- national agencies (opt-in via --country / --all) -------------------
    PortalEntry(
        "de_kfw",
        "KfW — Förderprodukte für Unternehmen (Germany, national scheme)",
        "https://www.kfw.de/inlandsfoerderung/Unternehmen/",
        ("kfw",),
        "national-scheme",
        language="de",
        country="de",
    ),
    PortalEntry(
        "nl_rvo",
        "RVO — Netherlands Enterprise Agency (national schemes)",
        "https://english.rvo.nl/",
        ("netherlands enterprise agency",),
        "national-scheme",
        country="nl",
    ),
    PortalEntry(
        "es_ico",
        "ICO — Spain: financing facilities for enterprises (national scheme)",
        "https://www.ico.es/en/web/ico_en/ico-loan-facilities",
        ("ico",),
        "national-scheme",
        country="es",
    ),
    PortalEntry(
        "at_aws",
        "aws — Austria Wirtschaftsservice (national schemes)",
        "https://www.aws.at/en/",
        ("aws",),
        "national-scheme",
        country="at",
    ),
    PortalEntry(
        "ie_enterprise_ireland",
        "Enterprise Ireland — supports for companies (national schemes)",
        "https://www.enterprise-ireland.com/en/supports",
        ("enterprise ireland",),
        "national-scheme",
        country="ie",
    ),
    PortalEntry(
        "lu_snci",
        "SNCI — Luxembourg: Société Nationale de Crédit et d'Investissement",
        "https://www.snci.lu/",
        ("snci",),
        "national-scheme",
        language="fr",
        country="lu",
    ),
    PortalEntry(
        "dk_eifo",
        "EIFO — Denmark: Export and Investment Fund (national schemes)",
        "https://www.eifo.dk/en/",
        ("eifo",),
        "national-scheme",
        country="dk",
    ),
    PortalEntry(
        "se_almi",
        "Almi — Sweden: loans and business development (national schemes)",
        "https://www.almi.se/en/in-english/",
        ("almi",),
        "national-scheme",
        country="se",
    ),
    PortalEntry(
        "fi_business_finland",
        "Business Finland — funding services (national schemes)",
        "https://www.businessfinland.fi/en/for-finnish-customers/services/funding",
        ("business finland",),
        "national-scheme",
        country="fi",
    ),
    PortalEntry(
        "it_invitalia",
        "Invitalia — Italy: incentives for enterprises (national schemes)",
        "https://www.invitalia.it/en",
        ("invitalia",),
        "national-scheme",
        country="it",
    ),
]


class VerificationError(ValueError):
    pass


def document_from_html(entry: PortalEntry, html: str) -> Document:
    text = html_to_text(html)
    if len(text.split()) < MIN_TEXT_WORDS:
        raise VerificationError(
            f"{entry.key}: only {len(text.split())} words extracted —"
            " JS-rendered shell, not ingesting"
        )
    lowered = text.lower()
    missing = [p for p in entry.verify if p.lower() not in lowered]
    if missing:
        raise VerificationError(
            f"{entry.key}: expected phrases not found: {missing}"
        )
    if entry.source_type == "national-scheme":
        words = text.split()
        if len(words) > EXCERPT_WORDS:
            text = (
                " ".join(words[:EXCERPT_WORDS])
                + f"\n\n[Excerpt — full and current details at {entry.url}]"
            )
    return make_document(
        title=entry.title,
        text=text,
        source_url=entry.url,
        source_type=entry.source_type,
        language=entry.language,
    )


def _cache_key(entry: PortalEntry) -> str:
    return f"{entry.key}.html"


def load_cached_documents() -> dict[str, Document]:
    """Verified Documents from the local cache only — no network."""
    docs: dict[str, Document] = {}
    for entry in REGISTRY:
        path = CACHE_DIR / _cache_key(entry)
        if not path.is_file():
            continue
        try:
            docs[entry.key] = document_from_html(
                entry, path.read_text(encoding="utf-8", errors="replace")
            )
        except (VerificationError, ValueError) as exc:
            logger.warning("skipping cached %s: %s", entry.key, exc)
    return docs


def pull(entries, *, pipeline=None, force=False):
    fetcher = PoliteFetcher(CACHE_DIR)
    results = []
    for entry in entries:
        try:
            html = fetcher.fetch(entry.url, _cache_key(entry), force=force)
            doc = document_from_html(entry, html)
        except RobotsDisallowed as exc:
            logger.error("SKIPPED %s: %s", entry.key, exc)
            results.append((entry, "SKIPPED — robots.txt disallows"))
            continue
        except VerificationError as exc:
            logger.error("SKIPPED %s", exc)
            results.append((entry, "SKIPPED — verification failed"))
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.error("SKIPPED %s: fetch failed: %s", entry.key, exc)
            results.append((entry, f"SKIPPED — fetch failed ({exc})"))
            continue
        if pipeline is None:
            results.append((entry, "verified (dry run)"))
        else:
            n = pipeline.ingest(doc)
            results.append((entry, f"{n} chunks" if n else "unchanged, skipped"))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--country",
        nargs="*",
        default=[],
        help="ISO codes of national agencies to include (e.g. de nl at)",
    )
    parser.add_argument("--all", action="store_true", help="include every entry")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="refetch cached pages")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    wanted = {c.lower() for c in args.country}
    entries = [
        e
        for e in REGISTRY
        if e.source_type == "ec-portal" or args.all or e.country in wanted
    ]

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
        print(f"  • [{entry.key}] {entry.title[:58]} — {status}")
    print(f"\n{len(results) - failures}/{len(results)} pages OK.")


if __name__ == "__main__":
    main()
