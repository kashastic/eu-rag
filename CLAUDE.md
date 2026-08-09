# CLAUDE.md — working notes for EURAG

Guidance for Claude Code sessions in this repo. Read once at session start.

## What this is

EURAG — a **citation-first RAG platform** answering EU compliance & funding
questions for SMEs. Every answer carries `[N]` citations that resolve to a
real chunk of an official text; if the corpus can't support an answer, it says
so instead of guessing. FastAPI backend + a Next.js web app. Corpus is 47
documents (31 EUR-Lex acts + EC portal pages + open-calls snapshot + 10
national funding agencies).

**Milestones M1–M6 are complete** and tagged `v1.0.0`, the **"make it live
safely" batch is complete** (all 8 phases), and **EURAG is LIVE** at
<https://eurag.duckdns.org> — GCP `e2-medium`, 47 documents, real Turnstile
keys, Let's Encrypt cert. Current state and open work:
[`context_files/HANDOFF.md`](context_files/HANDOFF.md); the batch's design
decisions (D1–D17) are in
[`context_files/PLAN_LIVE_SAFETY.md`](context_files/PLAN_LIVE_SAFETY.md).
The repo docs are the single source of truth (there is no external tracker —
do **not** look for Notion or similar). Start deep dives from
[`docs/WIKI.md`](docs/WIKI.md); the running build log with before/after
numbers is [`docs/DEVLOG.md`](docs/DEVLOG.md); dated decisions and gotchas —
**what was chosen and why, so it isn't silently reversed** — are in
[`docs/UPDATE_LOG.md`](docs/UPDATE_LOG.md).

## Commands

```bash
# env (Python 3.11+; venv already exists at .venv)
source .venv/bin/activate

# tests — 237 pass / 6 skip, fully offline (hash embedder, no API key needed)
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
EURAG_HYDE_MODEL=none python -m core.evaluation.harness   # PIN expansion or it is noise
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
| `core/retrieval/` | `bm25`, `vector_store` (Qdrant), `hybrid_retriever` (RRF + rerank + tenant scope), `reranker`, `expansion` (contextualise + HyDE) |
| `core/generation/` | `answerer` (cite-or-fail + insufficiency marker), `citations`, `llm_client` |
| `core/security/` | `auth` (JWT, RBAC, audit), `crypto` (AES-256-GCM), `pii` (upload gate) |
| `core/` | `pipeline` (wires it together), `registry` (SQLite, tenant+cipher), `db` (SQLite/Postgres), `conversations`, `quota`, `config` |
| `api/` | `main`, `deps` (auth/tenant/tier), `routes/*`, `middleware/` (ratelimit, headers) |
| `data/` | `scrapers/` (eurlex, portals, funding_calls, common), `samples/`, `seed.py` |
| `frontend/static/` | zero-dep chat UI (local single-user mode) |
| `frontend/web/` | Next.js app (accounts, saved chats, tiers) |
| `tests/` | unit + integration; `test_security`, `test_tiers`, `test_postgres` |

## How it works (the load-bearing bits)

- **Retrieval**: follow-up contextualised → HyDE-expanded query → BM25 + vector
  search → RRF fuse → cross-encoder rerank → cap 2 chunks/doc → top-k.
  Article-aware chunking (headings are hard boundaries; each chunk carries its
  "Article N —" heading).
- **Follow-ups**: a bare "what if I have 29 people?" has no topic of its own, so
  when `history` is present `pipeline.query` first rewrites it into a standalone
  question (`QueryContextualizer`, Haiku). **Contextualisation runs before
  HyDE** — expanding a fragment just amplifies the wrong topic. The rewrite is
  what retrieval *and* the answerer see, so the answerer never handles a
  fragment and cite-or-fail is untouched. Anonymous requests send their turns
  (capped, untrusted); `/conversations/{id}/messages` reads its own stored chat.
- **Generation**: model may use only the numbered sources, must cite every
  claim; uncited/mis-cited answers are regenerated then downgraded to verbatim
  quotes. **One exception**: an *honest refusal* may cite nothing — the model
  appends `INSUFFICIENT_SOURCES` when it can't answer, and a short uncited
  answer carrying that marker is valid (see Gotchas). The marker triggers the
  **escalation cascade** (a cheap primary model, one retry on a stronger model
  over deeper retrieval).
- **Per-query telemetry**: `pipeline.query` emits one `query outcome:` line per
  query — the denominator for escalation rate. `AnswerResult.insufficient_reason`
  records *why* an answer was insufficient (`marker` / `uncited` / `no_sources`);
  the escalation gate still reads `insufficient` alone.
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
6. Keep `docs/` current — the DEVLOG, UPDATE_LOG, DATA_SOURCES, SECURITY,
   PROJECT_PLAN are the project's memory. **Every non-obvious choice and every
   trap that cost you time gets an entry in
   [`docs/UPDATE_LOG.md`](docs/UPDATE_LOG.md)** — decision + reason, or symptom
   + fix. The measurements behind it go in the DEVLOG; link, don't duplicate.

## Gotchas (learned the hard way)

Short form — the standing list a session must know before touching anything.
Full reasoning, symptoms, and the decisions behind them:
[`docs/UPDATE_LOG.md`](docs/UPDATE_LOG.md).

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
- **A Linux deploy needs `chown -R 10001:10001 data/raw` before the first
  `up`.** `data/raw/` is gitignored, so a fresh clone doesn't have it and Docker
  creates the bind-mount source as `root:root`; the image runs as `USER eurag`
  (uid 10001) and the seeder dies in ~5s with `PermissionError` on
  `data/raw/.seed.lock`. **The macOS rehearsal cannot catch this** — Docker
  Desktop remaps bind-mount ownership, so it only ever fails on Linux.
- **Cold prod seed is ~3m20s** with a populated `data/raw` (4202 chunks embedded),
  plus ~6 min of scraping if the cache is empty. A re-run is ~4s — all 47
  documents hash-skip, nothing re-embeds (that's D5 holding), so
  `git pull && up -d` is cheap.
- **A rewrite step inherits the promises of whatever it feeds.** `answerer`
  promises to answer in the question's language — but it only sees the
  contextualiser's rewrite, so when that rewrite translated, an English
  question came back answered in Spanish. Before adding any pre-retrieval or
  pre-generation rewrite, re-read `answerer`'s prompt and carry every
  input-shaped promise into the new step.
- **`.env` is loaded by importing `core.config`** (import-time `_load_dotenv()`).
  A script that imports `core.generation.llm_client` directly gets no key,
  silently falls back to `ExtractiveClient`, and every LLM helper returns its
  no-op fallback — which looks exactly like a broken change. `import
  core.config` first, and **check `type(llm).__name__` before trusting any
  result**.
- **The harness is NOT deterministic with HyDE on.** Expansion calls Haiku, so
  each run gets a different hypothetical document → different candidate pool.
  A one-case metric delta (the golden set has only 3 compound cases, so one
  flip = 33pp) is *not* attributable to your change. Pin it before any A/B:
  `EURAG_HYDE_MODEL=none python -m core.evaluation.harness`.
- **`EURAG_RERANK_BATCH` is a memory ceiling, not a tuning knob.** fastembed
  defaults to 64, above every pool `hybrid_retriever` builds (`k*5`: 30 at
  `top_k=6`, **60 on the escalation path** at `EURAG_ESCALATION_TOP_K=12`), so
  the whole pool went through in one forward pass and allocated **1.6GB** —
  which OOM-killed the api container in prod (502s, no traceback, `dmesg` shows
  `killed process`). Default is now 8: same scores, 5.7× less transient memory,
  +0.44s. Raising `EURAG_ESCALATION_TOP_K` grows this peak.
- **`query outcome:` is the one unconditional per-query log line** — every other
  one fires on a branch, so it is the only denominator you have. Escalation rate
  = `grep -c "escalated=True"` over `grep -c "query outcome:"`; `primary_reason`
  says whether an escalation was a corpus gap (`marker`) or a citation-format
  failure (`uncited`). Keep grep-targeted log strings **ASCII** — a count of the
  escalation line once failed on the em dash in it.
- **An honest refusal is allowed to cite nothing** (marker present, ≤600 chars —
  `MAX_UNCITED_REFUSAL_CHARS`). Everything else uncited is still rejected:
  fabricated markers, long uncited bodies, and uncited answers with no marker.
  Don't "tighten" this back to always-require-a-citation — that rejected the
  model for obeying its own prompt and cost 4 LLM calls per refusal.
- **The Turnstile widget is invisible and runs at submit time**
  (`interaction-only` + `execution: "execute"`, one fresh single-use token per
  submit). **Never re-gate a button on it solving** — the old always-visible
  widget put a checkbox above the composer on every page load and left Ask *and*
  Create-account dead with no error whenever the challenge couldn't run.
- **The bot gate is skipped when no `EURAG_TURNSTILE_SECRET` is set**, so an
  auth-flow bug can be invisible locally and fatal in prod (that is how
  `/login` shipped unable to register). Rehearse with Cloudflare's test keys —
  sitekey `1x00000000000000000000AA` / `3x00000000000000000000FF`, secret
  `1x0000000000000000000000000000000AA` — passed **on the command line, never
  in `.env`**.
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

## Open work

The live-safety batch is closed and deployed; its phase-by-phase record is in
[`docs/DEVLOG.md`](docs/DEVLOG.md), not here. **Current state and the live
to-do list are in [`context_files/HANDOFF.md`](context_files/HANDOFF.md)** —
that file is the one to read at session start.

Standing gaps, in rough priority order:

1. **No billing alerts.** A public URL spends the server's Anthropic key on
   anonymous full-quality answers. `docs/DEPLOY.md` §5.6.
2. **Escalation cost — count it before tuning it.** The `query outcome:`
   telemetry exists for this and has **not been read against real traffic yet**.
   The open lever on escalation *count* is that nothing thresholds relevance, so
   an off-corpus question always reaches the LLM. Full list: HANDOFF item 3.
3. **Seed lock lives in a bind mount** — move `data/raw/.seed.lock` to
   `/app/var/.seed.lock` so a Linux deploy stops needing a manual
   `chown -R 10001:10001 data/raw`.
4. **Sizing numbers are macOS-measured** — `docs/DEPLOY.md` §4 should be
   re-measured on the Linux host.
5. **The 90-day credit cliff** (trial started 2026-08-08) — `docs/DEPLOY.md` §6.

Deferred: accounts have no email (so no password reset), Google OAuth
disabled, registry-uploads-per-instance, no streaming, no i18n, no monitoring.
