# Data Sources

Registry of everything that can enter the corpus. A source is not "in" until
it has a row here with licensing checked.

| Source | Type | Coverage | License / reuse | Status |
|---|---|---|---|---|
| Bundled excerpts (`data/samples/`) | curated text | Horizon/KfW/BPI funding summaries (GDPR + SME definition excerpts retired — superseded by full EUR-Lex texts) | © EU, reuse permitted with attribution (Decision 2011/833/EU) | ✅ shipped (M1 seed); 2 of 4 files retired 2026-07-05 |
| EUR-Lex | HTML (`data/scrapers/eurlex.py`) | 31 full acts: tiers 1–2 (GDPR, AI Act, DSA, DMA, NIS2, Data Act, ePrivacy, P2B, SME definition, late payment, consumer rights, GPSR, VAT, CSRD, whistleblower, accessibility) + second wave 2026-07-06 (CRA, e-Commerce, DSM copyright, trade secrets, UCPD, unfair contract terms, sale of goods, digital content, geo-blocking, product liability, services, working time, transparent working conditions, pay transparency, VAT small-enterprise scheme) | EU legal texts: free reuse with attribution (Decision 2011/833/EU) | ✅ pulled + title-verified, cached in `data/raw/eurlex/` |
| EC SME portal (single-market-economy.ec.europa.eu) | HTML | SME definition, funding programmes, guides | © EU, reuse permitted | M4 scraper |
| KfW (Germany) | HTML | national SME funding schemes | site ToS — scrape politely, store excerpts + link out | M4 scraper |
| BPI France | HTML | national SME funding schemes | site ToS — same policy | M4 scraper |
| National portals, wealthy Western EU (NL RVO, ES ICO/CDTI/ENISA, BE VLAIO+, AT aws, IE Enterprise Ireland/SBCI, LU SNCI, DK EIFO, SE Almi/Vinnova, FI Business Finland/Finnvera, IT Invitalia) | HTML | national SME funding schemes | per-site ToS check pending — same excerpt+link-out policy | candidate (countries user-approved 2026-07-05) |
| Live web (agentic) | search API | freshness checks only | per-result attribution, never stored in corpus | M5 |

## Rules
1. **Provenance is mandatory.** Every document records `source_url`,
   `source_type`, `language`, `fetched_at` at load time. Citations resolve to
   these fields; a document without provenance is rejected by the loader.
2. **Official text wins.** Where a national portal paraphrases an EU
   regulation, we ingest the EUR-Lex original and link the portal page as a
   related resource.
3. **Scrape politely.** Respect robots.txt, identify with a UA string,
   rate-limit, cache raw responses under `data/raw/` (gitignored).
4. **No personal data in the corpus.** From M3 the Presidio gate enforces
   this mechanically; until then, only official/public documents are ingested.
5. **Source links must resolve.** A citation that 404s is worse than no
   citation. After any corpus change, run
   `python -m infra.scripts.check_links` (checks every `source_url` in the
   registry; exits non-zero on breakage).

## Seed corpus
`python -m data.seed` ingests `data/samples/` plus every verified EUR-Lex
text cached in `data/raw/eurlex/` (pull once with
`python -m data.scrapers.eurlex`). Sample files whose law now exists as a
full EUR-Lex text are skipped automatically:
- `gdpr_key_articles.txt` — retired in favour of the full GDPR (32016R0679)
- `eu_sme_definition.txt` — retired in favour of Recommendation 2003/361/EC
- `eu_funding_overview.txt` — still active; replaced when the EC portal /
  Funding & Tenders scrapers land
- `national_schemes.txt` — still active; replaced by KfW / Bpifrance scrapers

On a fresh clone without the cache, the four samples still work alone, so
tests never need the network.

## EUR-Lex pull notes (2026-07-05)
- `eur-lex.europa.eu/robots.txt`: `/legal-content/*/TXT/HTML/` is allowed
  (only DOC and SIG formats are disallowed); `Crawl-delay: 10` — the scraper
  waits ≥10s between live requests and identifies itself via User-Agent.
- Every document is verified against expected title phrases before ingestion
  (EUR-Lex answers unknown CELEX ids with an HTTP 200 error page); mismatches
  are reported and skipped, never ingested.
