# DEVLOG

Running log of build sessions. Newest first.

## 2026-07-06 (later) — second EUR-Lex wave: corpus doubled to 31 acts
- 15 more official texts pulled and ingested (corpus: 33 docs / 5,296
  chunks). Chosen horizontal since the industries question is still open:
  CRA, e-Commerce Directive, DSM copyright, trade secrets, UCPD, unfair
  contract terms (93/13), sale of goods, digital content & services,
  geo-blocking, new Product Liability Directive (2024/2853), Services
  Directive, working time, transparent & predictable working conditions,
  pay transparency, VAT small-enterprise scheme (2020/285).
- Title verification caught a real bug: 32023L0970 "failed" because OJ
  markup writes "(EU)\xa02023/970" with a non-breaking space. Fixed in
  `html_to_text` (NBSP/unicode spaces → plain space) — this also cleans
  BM25 tokens corpus-wide, so NBSP-affected documents re-embedded.
- Golden set: +5 extended cases (CRA, sale-of-goods guarantee, 48-hour
  week, VAT exemption, pay-range information), phrases grep-verified.
- Harness on the doubled live corpus: doc_hit 100%, doc_mrr 0.98,
  phrase_hit 91% (22 cases) — quality held through 2.4x chunk growth.
- 75 tests passing; all 33 source links verified live.

## 2026-07-06 — coverage line + optional industry context
- Header now states current expertise: a "Current expertise: N official
  texts — data & digital · commerce & consumer · reporting · funding" line
  that expands to the full document list, fetched live from /documents so it
  never goes stale, plus an honest note that sector-specific regimes (food,
  machinery, textiles…) are not ingested yet.
- Industry input added as OPTIONAL, not a gate. Deliberate: the corpus is
  horizontal law only, so forcing an industry choice would add friction and
  imply sector expertise we don't have. Instead: dotted-underline field in
  the dock, remembered in localStorage, stamped on each query's file line.
- Plumbing: `industry` is an optional field on /query (≤80 chars). It is
  injected into the generation prompt only — retrieval never sees it (sector
  words would add BM25 noise over horizontal law). The prompt instructs the
  model to tailor where the sector matters and to say plainly when
  sector-specific EU rules are not among the sources. Survives escalation.
- Each industry submitted is logged server-side (`query industry context:`)
  — free research input for the open Tier-2 question of which sector law to
  ingest next.
- Verified live: "What safety rules apply to the products I sell?" as
  food & beverage → GPSR answer that explicitly notes the sources don't
  name food-specific rules. Tests: 74 passing.

## 2026-07-05 (UI) — markdown rendering fixed + "Official Journal" redesign
- Bug: the chat UI escaped answers and rendered them as plain text, so LLM
  markdown showed literally (`**bold**`). Added a minimal, safe markdown
  renderer (~40 lines, no deps): bold/italic/inline-code, headings, ul/ol,
  paragraphs. It runs on HTML-escaped text only, so no raw model output ever
  reaches the DOM. `[N]` markers are now clickable superscripts that scroll
  to and flash the matching footnote.
- Full visual redesign, "Official Journal, digitized": warm paper background
  with SVG grain, ink-navy serif body (Source Serif 4), Fraunces display,
  IBM Plex Mono for dossier chrome (query numbers, badges, status line),
  EU-gold accents, footnote-style sources with "official text ↗" links.
  Google Fonts is the one external fetch; falls back to Georgia offline.
- New response metadata surfaced: mode badge plus "escalated to stronger
  model" and "sources incomplete" badges (from the cascade fields).
- Verified in browser: bold renders, no literal `**`, marker→footnote flash
  works, empty state and mobile (375px) layouts clean, no console errors.

## 2026-07-05 (cascade) — low-confidence escalation: Sonnet answers, Opus rescues
- Cheap-by-default model cascade, user-requested. Every query is answered by
  the primary model (Sonnet 5). Only when that answer is low-confidence does
  a single retry run on `EURAG_ESCALATION_MODEL` (default claude-opus-4-8;
  "none" disables) — so the expensive model is paid for only on the queries
  that need it.
- Low confidence is detected mechanically, no LLM judge: the system prompt
  requires the model to end with the token INSUFFICIENT_SOURCES when the
  sources don't answer the core question (stripped before shipping, exposed
  as `insufficient` in the API response alongside `escalated`); answers that
  fail citation validation twice also count. Honest-insufficiency behavior is
  preserved: if the escalated answer is still insufficient, it ships flagged.
- The escalation retry retrieves differently, not just with a bigger model:
  k=12 with the per-doc cap raised 2→6. Rationale: the diverse first pass
  failed, so the retry goes deep — insufficiency usually means the right
  document was found but the answering passage sat below the per-doc cap.
  Measured: the GDPR Art. 37(1) chunk enters top-12 at max_per_doc=6.
- Verified live: the DPO question — one of the two known chunk-precision
  misses — now escalates and returns the actual Art. 37(1)(a–c) criteria,
  cited, insufficient=false. SME-thresholds stays on Sonnet un-escalated.
  This closes one of the two M2 known misses at the product level (the eval
  harness still measures single-pass retrieval, unchanged).
- Cost shape: non-escalated query = Sonnet only; escalated query ≈ Sonnet +
  Opus (~2.7x a Sonnet-only query at intro pricing, on a small minority of
  queries). No-API-key mode is unaffected (extractive, no cascade).
- Tests: 71 passing (marker detection/stripping, cascade triggers, honesty
  flag survives escalation, no-escalation paths).

## 2026-07-05 (cost) — answer model switched to Sonnet 5
- User asked for a cheaper model on the API path. The only LLM call site is
  answer generation (core/generation/llm_client.py via answerer); embeddings
  and the reranker are local ONNX, and the eval harness metrics are
  computed without an LLM — so there was nothing else to split yet.
- Default `EURAG_LLM_MODEL` changed claude-opus-4-8 → claude-sonnet-5
  ($3/$15 vs $5/$25 per MTok; $2/$10 intro until 2026-08-31 — roughly 60%
  cheaper right now). Generation is grounded in retrieved passages with
  enforced citations, so Sonnet-tier is the right fit; verified live —
  health shows anthropic:claude-sonnet-5, SME-thresholds answer correct
  and cited. Override per env: claude-haiku-4-5 ($1/$5) for cheapest,
  claude-opus-4-8 for max quality.
- Standing choice: future auxiliary LLM tasks (HyDE query rewriting, eval
  judging) default to claude-haiku-4-5 when they are built — cheap, high
  volume, quality-uncritical.

## 2026-07-05 (later still) — M2 part 1: eval harness + cross-encoder reranker
- `core/evaluation/golden.py` — the golden set moved out of the test file and
  became the shared source of truth (17 cases: 9 core + 8 extended). Each
  case now also pins verbatim phrases from the passage that actually answers
  the question (all phrase choices grep-verified against the ingested texts).
- `core/evaluation/harness.py` — `python -m core.evaluation.harness`
  measures doc_hit@k, doc MRR, and phrase_hit (chunk-level precision), with
  `--json` for before/after diffing. Tests keep enforcing the doc-level bar;
  the harness measures the chunk-level one.
- `core/retrieval/reranker.py` — fastembed cross-encoder reranking, wired
  into `HybridRetriever`: fused pool grows to ≥30 candidates, the
  cross-encoder reorders it, then the per-doc cap and top-k cut apply.
  `EURAG_RERANKER=none` disables; unavailable models degrade to no reranking.
- Numbers (live corpus, fastembed embedder, k=6):
  - baseline:        doc_hit 100%, doc_mrr 1.00, phrase_hit 82%
  - + ms-marco-L-6:  doc_hit 100%, doc_mrr 1.00, phrase_hit 88%, ~1s/query
  - Cross-encoder default is ON (`Xenova/ms-marco-MiniLM-L-6-v2`, 80MB
    one-time download). MiniLM-L-12 and jina-turbo measured too — no better
    on our failure cases; French golden question unharmed by the EN model.
  - Verified live: the 14-day-withdrawal question now retrieves the exact
    CRD passage (it missed before reranking).
- Remaining known misses (2/17): the GDPR Art. 37(1) DPO-triggers chunk and
  the Art. 6(1) lawful-bases chunk rank behind other *relevant* GDPR chunks
  (Arts. 38/39, recitals) and the 2-per-doc cap keeps only the top two.
  All models tested agree, so this is not a reranker-choice problem —
  candidates: query decomposition/HyDE, or a smarter per-doc budget.
- Tests: 63 passing (reranker plumbing unit tests use a fake reranker;
  conftest pins EURAG_RERANKER=none so the suite stays offline).

## 2026-07-05 (later) — broken source link fixed + corpus-wide link check
- User found a dead citation link. Audit of all 18 `source_url`s: the 16
  EUR-Lex links and the KfW link resolve (HTTP 200); the M1 hand-written
  funding-overview sample pointed at a non-existent EC page
  (`.../smes/sme-strategy/sme-funding-opportunities_en`, 404). Corrected to
  the real page: `https://single-market-economy.ec.europa.eu/access-finance_en`
  ("Access to finance"). Because `doc_id = hash(source_url|title)`, the fix
  required deleting the stale document from the live registry/vectors before
  re-seeding — a plain re-seed would have left an orphan with the dead link.
- New `python -m infra.scripts.check_links`: verifies every `source_url` in
  the registry resolves (read-only, rate-limited, exits non-zero on
  breakage). Run it after every corpus change; 18/18 ok now. Citations that
  404 are worse than no citation — this is now standing pull-session
  discipline alongside title verification.
- 59 tests still green; live query confirms citations carry the fixed URL.

## 2026-07-05 — EUR-Lex source pull: real corpus replaces hand-written excerpts
- New `data/scrapers/eurlex.py`: pulls the 16 tier-1/2 shortlist acts as
  HTML, caches raw responses in `data/raw/eurlex/` (gitignored), verifies
  each document against expected title phrases before ingesting (EUR-Lex
  returns HTTP 200 error pages for unknown CELEX ids — status codes prove
  nothing), ingests with `source_type="eur-lex"` and the real URL. Respects
  robots.txt `Crawl-delay: 10`, custom User-Agent, certifi CAs for the
  macOS-Python TLS gap. All 16 CELEX ids from the shortlist verified correct.
- `data/seed.py` now seeds samples + cached EUR-Lex texts; the hand-written
  GDPR and SME-definition excerpts are skipped once their full texts are
  cached (fresh clones without the cache still work offline on 4 samples).
- Live corpus rebuilt from scratch: 18 documents / ~3,900 chunks
  (was 4 documents / a few dozen chunks).
- Retrieval fix the bigger corpus forced: `HybridRetriever` now caps results
  at 2 chunks per document (with backfill for tiny corpora). Without it, one
  dominant act monopolized all top-k slots (e.g. every gatekeeper-question
  slot went to the DSA, crowding out the DMA), and an RRF quirk let noisy
  semantic rankings drown a decisive BM25 rank-1 hit that appeared in only
  one list. Also better for citation diversity generally.
- `html_to_text` treats whitespace-only lines as blank (EUR-Lex OJ markup).
- Tests: 59 passing (was 44). Golden set kept, plus 8 extended golden
  questions for the new acts (they skip on a fresh clone without the cache)
  and scraper unit tests (verification, error-page rejection, mismatch never
  reaches the pipeline).
- Verified live (port 8000, fastembed + Opus): DPO, AI Act high-risk, late
  payment interest, 14-day withdrawal, French SME thresholds — all answer
  from the right act with resolvable citations; chat UI shows 18 docs.
- Known limit for M2: chunk-level precision. Doc-level retrieval is right,
  but e.g. the GDPR Art. 37(1) chunk (when a DPO is mandatory) doesn't crack
  top-k for DPO questions — the reranker milestone should fix this; the
  chunk itself is present and clean (`e4ef45240752d7c3:252`).
- Open question for the user (blocks tier 3): which countries beyond DE/FR,
  and which industries, matter for national schemes and tier-2 additions?

## 2026-07-04 (later) — LLM mode live
- Added a no-dependency `.env` loader in `core/config.py` (+ `.env.example`);
  API key configured locally, generator now `anthropic:claude-opus-4-8`.
- Softened the extractive-mode preamble and added a "Quote mode" banner in the
  UI so running keyless no longer reads as an error.
- Verified in browser: DPO (Germany/BDSG nuance) and EIC Accelerator questions
  return mode=llm answers with resolvable citations.

## 2026-07-04 — M1 completed end-to-end
- Context pulled once from the Notion project board, then Notion retired as a
  dependency: the plan now lives in this repo (PROJECT_PLAN.md) and nowhere else.
- Security: expanded SECURITY.md with a ranked breach-scenario table and the
  standing rule — no multi-user deployment before the M3 security spine.
- Built the pieces the scaffold was missing:
  - `data/samples/` seed corpus (GDPR key articles, EU SME definition, EU
    funding overview, national schemes) with mandatory provenance headers
  - `data/seed.py` — `python -m data.seed` ingests the bundled corpus
  - `frontend/static/index.html` — zero-dependency chat UI with citation chips
  - test suite: unit (chunker, BM25, RRF, citations, loader, answerer),
    API integration, and a golden-question retrieval eval
- Verified: fresh venv → install → seed → tests green → cited answer in browser.

Next: M2 — RAGAS-style eval harness first, then reranker/HyDE with
before/after numbers.
