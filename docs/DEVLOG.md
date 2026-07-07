# DEVLOG

Running log of build sessions. Newest first.

## 2026-07-07 (M5) — Next.js app, saved chats, multi-instance backend
The production frontend + the persistence changes that make horizontal
scaling correct.
- **Next.js web app** (`frontend/web/`, App Router, React 19, no heavy UI
  deps — hand-styled in the Official Journal aesthetic): login/register with
  JWT + transparent refresh-on-401, a sidebar (new chat, saved-chat list,
  rename, delete), a chat pane rendering markdown answers with clickable
  citation footnotes and mode/escalated/insufficient flags, optional industry
  context. `npm run build` clean; verified live end-to-end in a browser
  (register → ask → GDPR answer citing Art. 37/39 → chat auto-titled and
  saved → survives reload → reopens full history from the server).
- **Saved chats backend**: `core/conversations.py` (create/list/get/append/
  rename/delete/erase, user-scoped, citations stored as JSON) + `/conversations*`
  routes; the ask-within-chat route runs the pipeline and persists both turns.
- **Multi-instance data layer** (`core/db.py`): a dialect-aware DB (SQLite
  local, Postgres via `EURAG_DATABASE_URL`). Ported auth + conversations onto
  it so users, refresh-token revocation, audit, and chat history are shared
  across every API instance. **Verified against a real Postgres** (docker):
  roles, single-use refresh, isolation, audit all identical — captured as
  opt-in `tests/test_postgres.py` (`EURAG_TEST_DATABASE_URL`).
- **Shared rate limiting**: `api/middleware/ratelimit.py` gained a Redis
  backend (atomic Lua token bucket, keys hashed) via `EURAG_REDIS_URL`; falls
  back to in-process, and fails open if the limiter errors.
- **CORS** (`EURAG_CORS_ORIGINS`) for split frontend/API dev.
- **Production stack**: `docker-compose.prod.yml` (Postgres + Qdrant + Redis +
  2 API replicas + web + Caddy single-origin proxy → no CORS, auto-HTTPS),
  web `Dockerfile` (Next standalone, non-root), `docs/DEPLOY.md`.
- Honest boundary documented: the official read-only corpus is replicated
  per-instance (reads correct everywhere); user-upload registry-on-Postgres is
  the one remaining port for cross-instance upload consistency (`core/db.py`
  makes it a driver swap). Auth port bug fixed on the way: dropped the
  SQLite-only audit triggers for app-layer append-only (portable to Postgres).
- Auth store rewritten onto the DB layer (was raw sqlite3); its test updated
  for the constructor + append-only-by-discipline. 157 tests + 2 Postgres
  parity (opt-in). M1–M6 done; M5 frontend + multi-instance shipped.

## 2026-07-07 (M6) — hardening & release: injection defense, rate limit, Docker, v1.0.0
- **Prompt-injection defense**: the answerer now fences retrieved text between
  BEGIN/END SOURCES markers and the system prompt states plainly that anything
  inside is untrusted data to cite, never instructions to obey. Tests verify
  the framing (injected text never hoisted above the fence; citation
  enforcement still holds if the model tries to comply) plus a live behavioural
  check — with a real key the model ignores an embedded "reply only PWNED" and
  answers the real question with a citation. The live test is opt-in
  (`EURAG_LIVE_TESTS=1`): it's network + stochastic and must never gate CI.
- **Rate limiting** (`api/middleware/ratelimit.py`): per-client token bucket on
  the two expensive routes (/query calls the LLM + can escalate to Opus;
  /ingest embeds). Keyed by bearer token when present else client IP, so one
  user can't drain another's budget. 429 + Retry-After. In-process (honest for
  single-instance; the interface is one allow() call for a Redis swap later).
  Default 30/min, burst 10; 0 disables.
- **Security headers** (`api/middleware/headers.py`): CSP, X-Content-Type-Options,
  X-Frame-Options DENY, Referrer-Policy on every response.
- **Docker self-host package**: multi-stage Dockerfile (non-root user,
  healthcheck), docker-compose.yml (one command, named volume for state,
  data/raw mount for the corpus), seed-on-first-boot entrypoint. Built and
  run-verified here: container boots, serves the 47-doc corpus, returns a
  cited answer, security headers present.
- Bug caught by the suite: I'd changed the API lifespan to read a module-level
  settings singleton, which froze auth_enabled at import time and broke
  per-test env overrides (5 auth tests). Fixed — lifespan reads settings
  fresh; only the import-time middleware gate uses the singleton.
- 151 tests (150 + 1 opt-in live). **v1.0.0 tagged.** M1–M6 all ✅ (M5
  agentic/Next.js frontend intentionally deferred — the static UI is polished
  and the plan's frontend rewrite would replace it; M6 load-testing/monitoring
  deferred as single-instance).

## 2026-07-06 (M3) — security spine: auth, tenant isolation, PII, crypto, erasure
The milestone that makes multi-user deployment safe. All controls are OFF by
default (`EURAG_AUTH_ENABLED` unset) so the local single-user experience is
byte-for-byte unchanged — turning them on is opt-in.
- **Auth** (`core/security/auth.py`): HS256 JWTs, 15-min access tokens, refresh
  tokens single-use (jti tracked, revoked on use → stolen refresh dies on
  reuse). scrypt passwords. First registered user = admin, rest = user.
- **Tenant isolation**: the kill-shot risk for a compliance product, so it's
  enforced in exactly one place — `api/deps.py::allowed_tenants` derives the
  readable set, `Registry.get_chunks(ids, tenants)` is the hard gate. Even an
  attacker who knows another tenant's chunk id gets [] back. Vector store
  filters by tenant server-side as a second layer; BM25 stays global but its
  foreign candidates die at the gate. Three adversarial tests.
- **PII gate** (`core/security/pii.py`): scans uploads BEFORE chunk/embed,
  REJECTS (doesn't silently redact — the uploader owns the fix), exempts
  official sources. Regex/Luhn default (email/phone/IBAN/card), Presidio
  optional. Findings are masked in the error, never echoed in full.
- **At-rest encryption** (`core/security/crypto.py`): AES-256-GCM of chunk
  text when EURAG_ENCRYPTION_KEY set, transparent at the registry boundary,
  version-prefixed so plaintext+encrypted rows coexist. Verified: with the
  key set, the plaintext never appears in the raw sqlite bytes.
- **Audit log**: append-only via SQLite triggers (UPDATE/DELETE raise).
  Query text stored as SHA-256 hash — queries can contain PII and erasure
  must never require editing the trail.
- **GDPR Art. 17 erasure**: per-document (owner or admin) and per-tenant
  (admin, account deletion) — deletes registry rows + vector points + live
  BM25 entries; idempotent; audited.
- New deps: pyjwt, cryptography. Registry schema gained tenant columns
  (from-scratch reseed required; done). New routes: /auth/*, /admin/*,
  DELETE /documents/{id}. 141 tests (was 104): +37 security incl. adversarial
  isolation, refresh-reuse, audit immutability, forged-token rejection,
  encryption-at-rest, API authz. Verified live (auth on, real key): unauth
  401 → register admin → cited answer → query audited as a hash.
- Remaining before production (M6): rate limiting, prompt-injection CI,
  load testing. Retrieval quality unchanged (M3 touches no ranking).

## 2026-07-06 (tier 3) — funding portals: EC pages, open calls, 10 countries
- New shared scraping infrastructure (`data/scrapers/common.py`):
  PoliteFetcher enforces robots.txt per host (incl. crawl-delay),
  rate-limits, caches under data/raw/, identifies with the project UA.
- `data/scrapers/portals.py` — registry-driven page scraper. EC-official
  pages pulled by default (full text, Decision 2011/833/EU); national agency
  pages are opt-in per country (standing rule: disabled by default) and
  store an EXCERPT (≤1,200 words) + link out, never a full mirror. Every
  page is phrase-verified and JS-shell-guarded (<100 words extracted → skip).
- `data/scrapers/funding_calls.py` — Funding & Tenders SEDIA search API
  (multipart form query; the JSON-body form silently ignores filters).
  Ingests ONE stable-identity snapshot doc of open/forthcoming SME-relevant
  grant calls (title, identifier, deadline, topic link) with the snapshot
  date and a verify-at-source note embedded. Re-running refreshes in place.
  M5's agentic layer replaces this with live lookups.
- Pulled: 3 EC pages + KfW (DE), RVO (NL), ICO (ES), aws (AT), Enterprise
  Ireland (IE), SNCI (LU), EIFO (DK), Almi (SE), Business Finland (FI),
  Invitalia (IT) + the calls snapshot. **Blocked (HTTP 403): Bpifrance (FR),
  VLAIO (BE), een.ec.europa.eu** — recorded in DATA_SOURCES; curated samples
  keep covering the Bpifrance/EEN headline facts.
- Corpus: 33 → 47 documents. Golden set +4 funding cases (29 total).
  Harness: doc_hit 100%, MRR 1.00, phrase_hit 93%, compound 100%.
- Golden markers learned "A|B" alternatives: the EEN question is now
  legitimately answered by EC portal pages, not just the old sample — the
  offline test caught that as a "failure" until the expectation was fixed.
- 104 tests passing.

## 2026-07-06 (M2 complete) — article-aware chunking, HyDE, decomposition
Each change measured on the harness (25 golden cases incl. 3 new compound
questions); merged only what the numbers justified.

| config | doc_hit | mrr | phrase | compound |
|---|---|---|---|---|
| before (para chunks @220w + reranker) | 100% | 0.98 | 91%* | 67% |
| article chunks @220w | 100% | 1.00 | 88% | 67% |
| article chunks @320w | 100% | 1.00 | 92% | 67% |
| + HyDE (haiku) — **shipped default** | 100% | 1.00 | 92% | 100% |
| + decomposition (haiku) | 100% | 1.00 | 92% | 67–100% (unstable) |

*91% measured against a phrase spec that turned out to reward the wrong
article (Pay Transparency Art. 7 vs Art. 5 for applicants); spec fixed.

- **Article-aware chunking**: "Article N" heading lines are hard chunk
  boundaries and every chunk carries its heading ("Article 37 — Designation
  of the data protection officer"). Budget raised 220→320 words: median
  article is 122 words, 77% of the corpus's 1,715 articles now fit in one
  chunk (still inside the reranker's 512-token window). This finally fixed
  the GDPR Art. 37(1) DPO miss — the answer is one whole chunk that leads
  with its own heading. Gotcha found on the way: `pipeline.ingest` skips
  unchanged content hashes, so chunker changes need a from-scratch reseed
  (`rm -rf var && python -m data.seed`) — first "measurement" was silently
  running on old chunks.
- **HyDE** (`core/retrieval/expansion.py`): Haiku drafts a 2–4 sentence
  hypothetical regulation passage; the vector leg embeds question+passage,
  BM25 keeps the raw question (regulation numbers must stay literal).
  Compound-question retrieval 67%→100%, stable across runs. One Haiku call
  per query (~1s, ~$0.0005). Default ON.
- **Decomposition** (same module): splits compound questions into
  sub-queries, RRF-merges their candidate pools, reranks against the
  original question. Measured honestly: no gain on top of HyDE — the
  reranker (scoring vs the original question) pushes sub-query candidates
  back down. Kept config-gated, default OFF.
- Remaining known misses (2/24 phrase cases): GDPR Art. 6 lawful-bases and
  Late Payment statutory-interest — in both, the right *document and
  article-family* is retrieved but the reranker prefers an adjacent slice
  (Art. 6 later paragraphs; recovery-costs Art. 6 instead of interest
  Art. 3). The escalation cascade covers these at answer time.
- 92 tests passing. M2 closed; next: M3 security spine or Tier-3 funding
  scrapers.

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
