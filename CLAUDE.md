# CLAUDE.md — working notes for EURAG

Guidance for Claude Code sessions in this repo. Read once at session start.

## What this is

EURAG — a **citation-first RAG platform** answering EU compliance & funding
questions for SMEs. Every answer carries `[N]` citations that resolve to a
real chunk of an official text; if the corpus can't support an answer, it says
so instead of guessing. FastAPI backend + a Next.js web app. Corpus is 47
documents (31 EUR-Lex acts + EC portal pages + open-calls snapshot + 10
national funding agencies).

**Milestones M1–M6 are all complete** and tagged `v1.0.0`. The **"make it live
safely" batch is complete** (all 8 phases, commit `ae5260d` — **committed but not
yet pushed**; the live deploy is blocked on the user creating a GCP VM) — resume from
[`context_files/HANDOFF.md`](context_files/HANDOFF.md); the approved plan with
all design decisions is [`context_files/PLAN_LIVE_SAFETY.md`](context_files/PLAN_LIVE_SAFETY.md).
The repo docs are the single source of truth (there is no external tracker —
do **not** look for Notion or similar). Start deep dives from
[`docs/WIKI.md`](docs/WIKI.md); the running build log with before/after
numbers is [`docs/DEVLOG.md`](docs/DEVLOG.md).

## Commands

```bash
# env (Python 3.11+; venv already exists at .venv)
source .venv/bin/activate

# tests — 209 pass, fully offline (hash embedder, no API key needed)
.venv/bin/python -m pytest -q
EURAG_LIVE_TESTS=1 pytest tests/test_hardening.py -q        # opt-in live LLM test
EURAG_TEST_DATABASE_URL=postgresql://… pytest tests/test_postgres.py   # opt-in PG parity

# corpus
python -m data.scrapers.eurlex        # pull the 31 EUR-Lex acts (cached in data/raw/)
python -m data.scrapers.portals --all # EC pages + national agencies
python -m data.scrapers.funding_calls # refresh the open-calls snapshot
python -m data.seed                   # ingest everything cached into the store
python -m data.seed --scrape --expect-docs 47   # deploy mode: fill missing caches, fail if short

# retrieval quality — REQUIRED after any retrieval change (see rules)
python -m core.evaluation.harness     # doc_hit@k, MRR, phrase_hit, compound_hit
python -m infra.scripts.check_links   # every source_url must resolve

# run backend (local single-user, no auth)
uvicorn api.main:app                  # → http://localhost:8000 (static UI)

# run the web app (needs the backend + auth enabled; see below)
cd frontend/web && npm install && npm run build   # or: npm run dev
```

## Layout

| Path | What |
|---|---|
| `core/ingestion/` | loader (`html_to_text`, provenance), article-aware `chunker`, `embedder` |
| `core/retrieval/` | `bm25`, `vector_store` (Qdrant), `hybrid_retriever` (RRF + rerank + tenant scope), `reranker`, `expansion` (HyDE) |
| `core/generation/` | `answerer` (cite-or-fail + insufficiency marker), `citations`, `llm_client` |
| `core/security/` | `auth` (JWT, RBAC, audit), `crypto` (AES-256-GCM), `pii` (upload gate) |
| `core/` | `pipeline` (wires it together), `registry` (SQLite, tenant+cipher), `db` (SQLite/Postgres), `conversations`, `quota`, `config` |
| `api/` | `main`, `deps` (auth/tenant/tier), `routes/*`, `middleware/` (ratelimit, headers) |
| `data/` | `scrapers/` (eurlex, portals, funding_calls, common), `samples/`, `seed.py` |
| `frontend/static/` | zero-dep chat UI (local single-user mode) |
| `frontend/web/` | Next.js app (accounts, saved chats, tiers) |
| `tests/` | unit + integration; `test_security`, `test_tiers`, `test_postgres` |

## How it works (the load-bearing bits)

- **Retrieval**: HyDE-expanded query → BM25 + vector search → RRF fuse →
  cross-encoder rerank → cap 2 chunks/doc → top-k. Article-aware chunking
  (headings are hard boundaries; each chunk carries its "Article N —" heading).
- **Generation**: model may use only the numbered sources, must cite every
  claim; uncited/mis-cited answers are regenerated then downgraded to verbatim
  quotes. The model appends `INSUFFICIENT_SOURCES` when it can't answer → that
  triggers the **escalation cascade** (a cheap primary model, one retry on a
  stronger model over deeper retrieval).
- **Tenancy**: every chunk has a tenant; `Registry.get_chunks(ids, tenants)` is
  the ONE hard gate. Official corpus = tenant `public`; each user gets a private
  tenant. Isolation is enforced in one place and adversarially tested.
- **Access tiers** (cost control): anonymous → N free full-quality questions
  counted **server-side per IP/day** (`core/quota.py`) → login wall. Logged-in
  free tier = Haiku, no escalation. **BYOK** = user's own key (AES-256-GCM
  encrypted) unlocks the full cascade on their bill. Local (auth-off) mode is
  untiered. Tiering lives in `api/deps.paid_tier` + `pipeline.query` overrides.

## Standing rules (follow these)

1. **Every retrieval change ships with before/after harness numbers.** Run
   `core.evaluation.harness` and record the delta in the DEVLOG. No exceptions.
2. **Citations must always resolve; keep the "not legal advice" framing.**
3. **National-portal scrapers are opt-in per country** (`--country`), store an
   excerpt + link-out (never a full mirror), and respect robots.txt.
4. **No secrets in the repo.** `.env`, `.env.local`, `var/`, `data/raw/`,
   `.claude/settings.local.json` are gitignored. JWT secret / encryption key /
   API keys come from env only.
5. **Commit/push only when asked.** End commit messages with the
   `Co-Authored-By: Claude …` line. `main` is the default branch.
6. Keep `docs/` current — the DEVLOG, DATA_SOURCES, SECURITY, PROJECT_PLAN are
   the project's memory.

## Gotchas (learned the hard way)

- **Chunker or registry-schema changes need a full reseed.** `pipeline.ingest`
  skips documents whose content hash is unchanged, so editing the chunker is a
  silent no-op on the existing store — run `rm -rf var && python -m data.seed`,
  or you'll "measure" the old chunks.
- **`api/main.py` reads settings twice on purpose.** Module-level `_settings`
  (import time) configures middleware — rate limiter, CORS — which is attached
  before any request, so those need a process restart to change. The lifespan
  reads settings *fresh* for per-request config (auth, pipeline). Don't collapse
  them; doing so froze `auth_enabled` and broke per-test env overrides once.
- **`data/raw/` is gitignored** → a clean clone / CI build seeds only the 4
  sample docs, not 47. Fixed for deploys: `python -m data.seed --scrape
  --expect-docs 47` fills the caches and fails loud if short; the prod compose
  runs this in a one-shot `seeder` service before the API replicas start.
- **`EURAG_STRICT_BOOT=true` (prod) makes degradation fatal**: an embedder
  that can't load its model raises instead of silently falling back to hash
  (which would poison shared Qdrant — same dim, undetectable), and a failed
  seed kills the container instead of serving 4 docs.
- **Anonymous quota is per client IP.** In local dev, `curl` and the browser
  share `127.0.0.1`, so curl-testing spends the browser's free questions —
  reset with a fresh DB (`rm var/eurag.sqlite3`).
- **`EURAG_TRUST_PROXY` decides what "client IP" means** for both the anon
  quota and the rate limiter (one helper: `api/deps.peer_ip`). Off (default)
  = the peer address; on = the first `X-Forwarded-For` hop. Set it **only**
  behind a proxy that rewrites the header (prod compose does) — on a directly
  reachable API a forged header would mint unlimited free questions. Behind a
  proxy with it off, every visitor shares one bucket.
- **Never put the prod-compose secrets in `.env`.** `pydantic-settings` reads
  `.env`, so an `EURAG_TURNSTILE_SECRET` there switches the bot gate on for the
  *test suite* too and 13 tests fail with `KeyError: 'access_token'` (register
  starts demanding a token). CI stays green because CI has no `.env`, so this
  only ever bites locally. The local prod rehearsal therefore keeps its secrets
  in **`.env.prod.local`** and passes them explicitly:
  `docker compose -f docker-compose.prod.yml --env-file .env.prod.local up -d`.
  On a VPS plain `.env` is fine (nobody runs pytest there).
- **`EURAG_DOMAIN` must be defaulted at the compose layer**, not left to the
  Caddyfile's `{$EURAG_DOMAIN::80}`. Caddy applies that default only when the
  variable is *unset*; an env var set to the **empty string** collapses the site
  address and Caddy dies with `unrecognized global option: encode`. The caddy
  service passes `EURAG_DOMAIN: ${EURAG_DOMAIN:-:80}` — and it needs an
  `environment:` block at all, or the domain never reaches the container and
  auto-HTTPS can never engage.
- **macOS Docker**: the daemon isn't running by default — `open -a Docker` and
  wait before any `docker` command.
- **Cold prod seed is ~3m20s** with a populated `data/raw` (4202 chunks embedded),
  plus ~6 min of scraping if the cache is empty. A re-run is ~4s — all 47
  documents hash-skip, nothing re-embeds (that's D5 holding), so
  `git pull && up -d` is cheap.
- **HyDE / expansion default ON** (Haiku); decomposition is built but ships OFF
  (measured: no gain over HyDE).
- Two known phrase-precision misses (GDPR Art. 6 lawful-bases, Late Payment
  statutory-interest) retrieve the right doc but an adjacent slice; the
  escalation cascade covers them at answer time.

## Deploy modes

- **Local single-user**: `uvicorn api.main:app` (auth off, SQLite, embedded
  Qdrant, static UI). Nothing to configure.
- **Single container**: `docker compose up`.
- **Multi-instance**: `docker-compose.prod.yml` (Postgres + Qdrant + Redis +
  one-shot `seeder` + API replicas + web + Caddy single-origin). The seeder
  scrapes+seeds into shared `apivar`/`modelcache` volumes before any replica
  starts — replicas never scrape, seed, or download models. Shared state:
  Postgres (`EURAG_DATABASE_URL`) for users/sessions/audit/chats, Redis
  (`EURAG_REDIS_URL`) for rate limits, Qdrant server for vectors. Full config
  reference in [`docs/DEPLOY.md`](docs/DEPLOY.md); threat/cost model in
  [`docs/SECURITY.md`](docs/SECURITY.md). Rehearse it locally with
  `docker compose -f docker-compose.prod.yml --env-file .env.prod.local up -d`
  (see the `.env` gotcha below for why the secrets don't live in `.env`).

## Known production gaps (not yet done)

Being closed by the in-progress batch ([`context_files/PLAN_LIVE_SAFETY.md`](context_files/PLAN_LIVE_SAFETY.md)).
Blockers first:
1. ~~**Corpus reproducibility**~~ — **DONE (Phase 1, 2026-07-25)**: seeder
   service + `--scrape --expect-docs 47` + strict boot.
2. ~~**Bot protection**~~ — **DONE (Phase 2, 2026-07-25)**: Turnstile gates
   anonymous questions + registration; sitekey served via `/healthz`, so real
   keys are a deploy-time env change. (No global $ ceiling — deliberate
   product choice.)
3. ~~**Startup guard**~~ — **DONE (Phase 3, 2026-07-25)**: `validate_startup`
   raises at import on auth+Postgres without `EURAG_JWT_SECRET`; warns when
   `EURAG_ENCRYPTION_KEY` is unset.
4. ~~**Prod images/compose not run-verified**~~ — **DONE (Phase 7, 2026-07-25)**:
   both images build; the full prod stack was brought up locally and smoke-tested
   end to end (47 docs, tiers, Turnstile, error mapping, rate limit, saved chats,
   replica kill). Two real bugs found and fixed — see Gotchas. **Still open: the
   VPS deploy itself** (needs a host, a domain, and real Turnstile keys).
Also done: ~~LLM-call error handling~~ (Phase 4, 2026-07-25 — failures map to
400 `byok_key_rejected`/503 + Retry-After instead of raw 500s; anon quota
refunds on failure; escalation is best-effort); ~~ingest caps, `anon_quota`
growth, XFF trust, `public/.gitkeep`~~ (Phase 5, 2026-07-25); ~~no CI~~
(Phase 6, 2026-07-25 — `.github/workflows/ci.yml`: pytest on py3.11 /
`npm ci && npm run build` on node 22 / postgres:16 parity, on push+PR).
Deferred beyond the batch:
registry-uploads-per-instance, accounts have no email (no password reset),
Google OAuth disabled, no streaming/i18n, no monitoring.
