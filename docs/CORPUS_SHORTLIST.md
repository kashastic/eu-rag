# Corpus shortlist — sources to pull (M4 prep)

Working list for the source-pull session. EUR-Lex CELEX IDs below are from
memory and mostly reliable, but the pull session must verify each one resolves
before ingesting (a wrong CELEX silently fetches the wrong law).

> **Status 2026-07-05:** tiers 1 and 2 pulled and ingested — all 16 CELEX IDs
> below resolved and passed title verification (`data/scrapers/eurlex.py`).
> Tier 3 is on hold pending the country/industry question (rule 6).
>
> **Status 2026-07-06 (tier 3):** EC portal pages (3), Funding & Tenders
> open-calls snapshot, and 10 of 12 national agencies pulled
> (`data/scrapers/portals.py`, `data/scrapers/funding_calls.py`).
> Bpifrance and VLAIO are bot-walled (403) — headline facts stay covered by
> the curated samples.
>
> **Status 2026-07-06:** second wave added — 15 more horizontal acts (see
> tables below). Chosen horizontal because the industries question is still
> open: e-commerce & consumer contract law, employment basics, services,
> product liability, VAT small-enterprise scheme, CRA. Sector law waits for
> the industries answer (or the logged `query industry context:` data).

Fetch pattern per document:
`https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:<ID>`
(swap `EN` for other languages later; start English-only.)

## Tier 1 — data & digital compliance (pull first)
| Regulation | CELEX (verify) |
|---|---|
| GDPR | 32016R0679 |
| ePrivacy Directive | 32002L0058 |
| AI Act | 32024R1689 |
| Digital Services Act | 32022R2065 |
| Digital Markets Act | 32022R1925 |
| NIS2 (cybersecurity) | 32022L2555 |
| Data Act | 32023R2854 |
| Platform-to-Business Regulation | 32019R1150 |
| Cyber Resilience Act *(added 2026-07-06)* | 32024R2847 |
| e-Commerce Directive *(added 2026-07-06)* | 32000L0031 |
| Copyright in the DSM *(added 2026-07-06)* | 32019L0790 |
| Trade Secrets Directive *(added 2026-07-06)* | 32016L0943 |

## Tier 2 — running an SME
| Regulation | CELEX (verify) |
|---|---|
| SME definition (Recommendation) | 32003H0361 |
| Late Payment Directive | 32011L0007 |
| Consumer Rights Directive | 32011L0083 |
| General Product Safety Regulation | 32023R0988 |
| VAT Directive | 32006L0112 |
| CSRD (sustainability reporting) | 32022L2464 |
| Whistleblower Directive | 32019L1937 |
| Accessibility Act | 32019L0882 |
| Unfair Commercial Practices Directive *(added 2026-07-06)* | 32005L0029 |
| Unfair Contract Terms Directive *(added 2026-07-06)* | 31993L0013 |
| Sale of Goods Directive *(added 2026-07-06)* | 32019L0771 |
| Digital Content & Services Directive *(added 2026-07-06)* | 32019L0770 |
| Geo-blocking Regulation *(added 2026-07-06)* | 32018R0302 |
| Product Liability Directive (new) *(added 2026-07-06)* | 32024L2853 |
| Services Directive *(added 2026-07-06)* | 32006L0123 |
| Working Time Directive *(added 2026-07-06)* | 32003L0088 |
| Transparent & Predictable Working Conditions *(added 2026-07-06)* | 32019L1152 |
| Pay Transparency Directive *(added 2026-07-06)* | 32023L0970 |
| VAT Small Enterprise Scheme *(added 2026-07-06)* | 32020L0285 |

## Tier 3 — funding & other portals
- EC SME portal pages (single-market-economy.ec.europa.eu) — SME strategy,
  access to finance, EEN
- Funding & Tenders portal — public search API for open calls (better than
  scraping HTML)
- National schemes — polite scraping rules per DATA_SOURCES.md, disabled by
  default. **Country scope decided by user 2026-07-05: wealthy Western
  European EU members** (NL and ES named explicitly). Candidate agencies —
  verify each agency/URL and its ToS in the pull session:

| Country | Agency (candidate) |
|---|---|
| Germany | KfW |
| France | Bpifrance |
| Netherlands | RVO (business.gov.nl has English SME pages) |
| Spain | ICO; CDTI and ENISA for innovation/startup loans |
| Belgium | regional: VLAIO (Flanders), Wallonie Entreprendre, hub.brussels |
| Austria | aws (Austria Wirtschaftsservice) |
| Ireland | Enterprise Ireland; Local Enterprise Offices; SBCI |
| Luxembourg | SNCI; Luxinnovation |
| Denmark | EIFO (ex-Vækstfonden/EKF) |
| Sweden | Almi; Vinnova |
| Finland | Business Finland; Finnvera |
| Italy | Invitalia; SME Guarantee Fund (Mediocredito Centrale) |

  Norway and Switzerland are wealthy Western European but non-EU — out of
  scope by default (different regulatory frame); ask if wanted.

## Rules for the pull session
1. Verify each CELEX resolves to the expected title before ingesting.
2. Store raw HTML under `data/raw/` (gitignored) so re-chunking never re-fetches.
3. Rate-limit (≥1s between requests), identify with a UA string.
4. Ingest via the existing loader so provenance headers are mandatory.
5. Replace the four hand-written `data/samples/` files with the real texts,
   keeping the same doc titles so golden tests keep passing (or update them).
6. Country scope: ANSWERED 2026-07-05 — wealthy Western European EU members
   (see Tier 3 table). Still open: which industries matter? (Affects Tier 2
   additions like food, textiles, machinery law.)
