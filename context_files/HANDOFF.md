# HANDOFF — continue here

**As of:** 2026-08-08 · last commit `5430a3b` (pushed, `origin/main` in sync) ·
tag `v1.0.0` · **209 tests pass, 3 skipped** · **live-safety batch Phases 1–7
of 8 done, Phase 8 partly done — ALL OF IT STILL UNCOMMITTED** (~650 lines
across 25 tracked files + several untracked). Full plan with every design
decision: [`PLAN_LIVE_SAFETY.md`](PLAN_LIVE_SAFETY.md) (in this directory).

### Read this first (state in 6 lines)

- **Nothing is running.** The prod stack was torn down with `down -v` — no
  containers, no volumes, no smoke-test data. Images `eurag-api` / `eurag-web`
  are still built locally (`linux/arm64`).
- **Phase 7 (prod bring-up) is DONE and green locally** — full smoke test passed,
  two real bugs found and fixed. Details below.
- **Phase 8 is IN PROGRESS**: `docs/DEPLOY.md` is rewritten; **still to do —
  `docs/DEVLOG.md` batch entry, `docs/SECURITY.md`, then the single commit.**
- **The live deploy is blocked on the user** creating a GCP VM (see §2 of
  "What's left"). Oracle is out — signup rejected.
- **Do not commit or push unless asked** (standing rule 5).
- If anything here contradicts [`CLAUDE.md`](../CLAUDE.md), CLAUDE.md wins on
  *standing* guidance; this file wins on *current state*.

**Phase 1 (corpus reproducibility) shipped 2026-07-25:** `data.seed` CLI
(`--scrape` fills missing caches, `--expect-docs N` fails deploys short of the
corpus, flock serializes replicas), one-shot `seeder` service in
`docker-compose.prod.yml` + shared `apivar`/`modelcache` volumes (api replicas
no longer re-seed or re-download models), `EURAG_STRICT_BOOT` (embedder
fallback raises, seed failure fatal), funding snapshot date from cache mtime
(hash now stable — no daily re-embed), registry WAL+busy_timeout, healthcheck
start-period 180s. Verified: 170 tests green, `--expect-docs 47` exit 0,
both compose files valid. NOT yet done: prod image build/bring-up (Phase 7).

**Phase 2 (Turnstile) shipped 2026-07-25** per D6–D10: `core/security/
turnstile.py` (`verify()` — no-network reject on missing token, fail-open on
CF outage, monkeypatchable `_post` seam), gate before `anon_quota.consume` in
`api/routes/query.py` (403 `turnstile_failed`, no quota burn) and on
`/auth/register` (D9), sitekey served via `/healthz` (D6 — key rotation is env
only), `frontend/web/components/Turnstile.tsx` (explicit render, single-use
tokens reset after every question) wired into the anon composer + register
modal, config `EURAG_TURNSTILE_SECRET`/`EURAG_TURNSTILE_SITEKEY` (unset =
off — local mode untouched), prod compose + `.env.example` updated. Verified:
183 tests green (13 new), `npm run build` clean, and a live uvicorn round-trip
against the **real** Cloudflare siteverify with the universal test keys —
always-pass secret + dummy token → 200 answer, always-fail secret → 403, no
token → 403 without a network call.

**Phase 3 (startup secret guard) shipped 2026-07-25** per D17:
`validate_startup(settings, db_url)` in `core/config.py`, called at import
time in `api/main.py` right after `_settings` — raises on auth+Postgres
without `EURAG_JWT_SECRET`, warns when `EURAG_ENCRYPTION_KEY` is unset, local
zero-config untouched. Verified: 6-test matrix on the helper +
`EURAG_AUTH_ENABLED=true EURAG_DATABASE_URL=postgresql://x/x python -c
"import api.main"` → RuntimeError.

**Phase 4 (LLM error handling + quota fairness) shipped 2026-07-25** per
D11/D12: `LLMUnavailableError(kind)` raised from `AnthropicClient.complete`
(auth / rate_limited / overloaded / network / upstream), app-level exception
handler in `api/main.py` (auth → 400 `byok_key_rejected`, rest → 503
`llm_unavailable` + `Retry-After: 10` — covers /query and conversations),
`AnonQuota.refund()` + consume-then-refund in the anon query branch,
escalation is best-effort (a failed retry keeps the primary answer), and the
request-scoped-escalation crash fixed (log read `self.escalation_llm.name`,
None on BYOK-only). Verified: mapping matrix with offline-constructed SDK
exceptions, route-level 400/503 + refund, refund-at-zero no-op.
**Phase 5 (hardening odds & ends) shipped 2026-07-25** per D14/D15: `/ingest`
field caps (text 500k, url 2000, type 40, lang 16 — fixed constants),
`AnonQuota._sweep` prunes rows older than 2 days inside the existing consume
transaction (insert branch only, so steady-state consumes stay one UPDATE),
`frontend/web/public/.gitkeep` (unblocks the web image build), `.gitignore`
`.env.*` + `!.env.example` + `!.env.local.example` (the second negation is
required — `frontend/web/.env.local.example` is tracked), and
`EURAG_TRUST_PROXY` (default off, `"true"` in prod compose). **Scope note:**
D15 named only the rate limiter, but `api/deps.client_ip` — the *anon quota*
key — was trusting `X-Forwarded-For` unconditionally, i.e. a forged header
bought unlimited free full-quality questions on any directly reachable
deploy. Both now share one helper, `deps.peer_ip(request, trust_proxy)`. That
flipped the old `test_anonymous_quota_is_per_ip_not_shared` (it asserted the
unsafe behaviour); it is replaced by trusted/untrusted matrices on both the
quota and the limiter. Verified: 209 tests green (+7 new, −1 replaced),
`npm run build` clean. NOT run: the web *image* build (needs the Docker
daemon — it is the Phase 7 bring-up step; the `public/` blocker itself is gone).

**Phase 6 (CI) shipped 2026-07-25** per D16: `.github/workflows/ci.yml` — three
parallel jobs on push-to-`main` / PR / manual, `concurrency` cancels superseded
runs, `permissions: contents: read`. **python**: ubuntu + py3.11 (matches the
API image; local dev is 3.13), pip cache keyed on `pyproject.toml`,
`pip install -e ".[dev]"`, `pytest -q` — offline by design, no secrets needed.
**web**: node 22 (matches `frontend/web/Dockerfile`), npm cache on
`frontend/web/package-lock.json`, `npm ci && npm run build`. **postgres**:
`postgres:16` service with a `pg_isready` healthcheck, `.[dev,prod]`,
`EURAG_TEST_DATABASE_URL=postgresql://postgres:eurag@localhost:5432/eurag
pytest tests/test_postgres.py -q`. Note that file *skips itself* when the URL
is unset or unusable, so a broken service would pass green — an explicit
`psycopg.connect(...)` step runs first and fails the job instead. README got a
CI badge. Verified: 209 passed / 3 skipped locally, workflow YAML parses,
`npm ci --dry-run` clean (lockfile in sync). NOT verified locally: the
postgres job end-to-end (needs a throwaway PG container — declined this
session) and py3.11 (no 3.11 interpreter on this machine) — both first prove
themselves on the initial CI run in Phase 8.

**Phase 7 (prod bring-up) — local rehearsal shipped 2026-07-25.** Both images
build (`eurag-api`, `eurag-web` — the web `public/` blocker is confirmed gone),
and the full `docker-compose.prod.yml` stack ran locally end to end with
`--env-file .env.prod.local`.

*Bring-up:* seeder cold run **47 documents, exit 0, ~3m20s** (populated
`data/raw`, 4202 chunks, no scraping); both api replicas **healthy in ~12s**
after it (they never seed and never re-download models — the shared
`apivar`/`modelcache` volumes doing their job, D3). `/healthz`:
`documents=47`, `embedder=fastembed:…MiniLM-L12-v2` (strict boot held — no
silent hash fallback), `auth_enabled=true`, `encryption=true`,
`turnstile_sitekey` served (D6 — key rotation really is env-only).

*Smoke, all green:* anon `/query` with no Turnstile token → **403
`turnstile_failed` with the quota untouched**; with a token → cited answers;
quota exhausted → **401 `anonymous_limit_reached`**; `/auth/register` → 403
without a token, 200 with (D9); login + `/auth/me` across replicas (shared JWT
secret); free tier → `tier: free`, `escalated: false`; real BYOK key →
`tier: byok` with the cascade firing (`escalated: true` on the known Late-Payment
phrase miss); **bad BYOK key → 400 `byok_key_rejected`**, not a 500 (Phase 4
verified in prod); key at rest is `enc1:…` ciphertext, `LIKE 'sk-ant%'` = false;
`audit` rows for `account.byok_set` / `auth.login` / `auth.register` / `query`;
saved chat + messages persisted in Postgres and readable from **every** replica;
rate limiter → exactly 10 (burst) then **429 + `Retry-After: 2`**, buckets in
Redis under `eurag:rl:*` (so cross-replica).

*Cross-replica proof:* Caddy round-robined 6 `/query` calls 2/4 across the two
replicas while the per-IP anon budget still held centrally in Postgres.

*Security check worth keeping:* a **forged `X-Forwarded-For`** on the anon
endpoint did **not** mint a fresh quota key — Caddy replaces the client-supplied
header, the request keyed to the real IP and hit the wall. That empirically
confirms D15's load-bearing assumption ("Caddy ≥2.5 strips client XFF"), which
until now was only assumed. `EURAG_TRUST_PROXY=true` is safe *behind this Caddy*.

*Resilience:* `docker kill` one replica → 10/10 `/healthz` 200 **and** a real
cited answer served (Docker DNS drops the dead container from the `api`
upstream); the replica rejoined healthy in ~20s with **no re-seed, no model
re-download**. Re-running the seeder ("`git pull && up -d`") took **4s, 47/47
hash-skipped, zero re-embeds** — D5 confirmed, the update path is cheap.

**Two real bugs found and fixed in Phase 7:**
1. `docker-compose.prod.yml` — the `caddy` service had **no `environment:`
   block**, so `EURAG_DOMAIN` from the env file never reached the container and
   `{$EURAG_DOMAIN::80}` always fell back to `:80`: **auto-HTTPS could never
   have engaged on the VPS.** Fixed with `EURAG_DOMAIN: ${EURAG_DOMAIN:-:80}`.
   The `:80` default *must* be supplied at the compose layer — Caddy applies its
   own default only when the var is **unset**, and an empty string collapses the
   site address (verified: `caddy adapt` dies with `unrecognized global option:
   encode`). Both paths re-verified via `compose config`.
2. Putting the prod secrets in **`.env` broke 13 tests** — `pydantic-settings`
   reads `.env`, so `EURAG_TURNSTILE_SECRET` switched the bot gate on inside the
   suite and registration stopped returning `access_token`. Not a code
   regression (24/24 pass with the two vars blanked), but a nasty local-only
   trap: CI has no `.env` and stays green. The rehearsal secrets now live in
   gitignored **`.env.prod.local`**, passed with `--env-file`; `.env` is back to
   just `ANTHROPIC_API_KEY`. Full suite re-verified: **209 passed, 3 skipped**.
   *Optional follow-up (not done, not in the plan):* make `tests/conftest.py`
   ignore `.env` so the suite is immune to whatever a dev has configured.

**NOT verified in Phase 7:** the browser-level UI pass (no browser in that
session) — every API path behind it was exercised with curl, and the built web
bundles do reference `challenges.cloudflare.com/turnstile/v0/api.js?render=explicit`
in both the chat and login chunks, but the widget was never actually rendered
and clicked. Also unrun: the live deploy (blocked on inputs). Noted while
reading logs: fastembed now warns it uses **mean pooling instead of CLS** for
this model — internally consistent here (seeder and api share the image, and the
prod stack seeded its own Qdrant), but vectors would differ from fastembed 0.5.1.

**Next: Phase 8** — docs (DEVLOG batch entry, DEPLOY.md rewrite incl. the VPS
runbook + backups, SECURITY.md, `.env.example`) then the single commit. The
plan's Phase 7 VPS runbook steps are still unwritten in `docs/DEPLOY.md`;
the numbers above are what it should record.

This is a point-in-time "pick up where we stopped" note. For standing repo
guidance (commands, conventions, gotchas) read [`CLAUDE.md`](CLAUDE.md); for the
full build history read [`docs/DEVLOG.md`](docs/DEVLOG.md).

---

## Where we are

M1–M6 complete and tagged. The last thing built and **verified live in a
browser** was the **access-tier / anti-abuse system**:
- Anonymous users get N (default 3) full-quality questions, counted
  **server-side per IP/day** (`core/quota.py`) → a login wall.
- Logged-in free tier = Haiku (no escalation); **BYOK** (own Anthropic key,
  AES-256-GCM encrypted) unlocks the full Sonnet→Opus cascade on the user's bill.
- Verified: 2 anon answers → wall → register → free-tier banner → add key →
  premium. Raw key never hits the DB; `account.byok_set` audited.

A production-readiness gap list was then produced and turned into the
live-safety batch below. **Phases 1–7 of that batch are built** (see the
per-phase notes above); every gap it names is closed except the live deploy.

Nothing is running — the prod stack was torn down with `down -v`, so no
containers, no volumes, and none of the smoke-test state (the `smoke1` account,
its chat, the rehearsal secrets) survive. A fresh `up` re-seeds from scratch
(~3m20s). Uncommitted: everything from Phases 1–7 plus the untracked
`CLAUDE.md` and `context_files/` — the batch commits as one unit in Phase 8.

## Resume in 60 seconds

```bash
cd /Users/akashacharya/Claude_Arena/EU_RAG && source .venv/bin/activate
.venv/bin/python -m pytest -q        # expect: 209 passed, 3 skipped
git status --short                   # expect: the whole uncommitted batch

# rehearse the full prod stack (needs Docker: open -a Docker, wait)
docker compose -f docker-compose.prod.yml --env-file .env.prod.local up -d
curl -s http://localhost/healthz     # want documents=47, embedder=fastembed:…
docker compose -f docker-compose.prod.yml --env-file .env.prod.local down -v

# retrieval quality: python -m core.evaluation.harness
```
`.env.prod.local` is gitignored and already populated (rehearsal secrets +
Cloudflare universal test keys). **Never move those into `.env`** — see the
gotcha in CLAUDE.md; it fails 13 tests.

## What's left in the batch

All six original items (corpus reproducibility, Turnstile, startup guard, prod
bring-up, LLM error handling, CI) are **built** — the per-phase notes at the top
are the record; the full spec is in [`PLAN_LIVE_SAFETY.md`](PLAN_LIVE_SAFETY.md).
No phase touched retrieval, so the harness rule never applied.

Remaining:

1. **Phase 8 — docs + commit.** `docs/DEPLOY.md` **rewritten** (73 → ~290 lines,
   2026-07-30, restructured 2026-08-08): seeder + boot order with measured
   timings, shared-state table incl. `anon_quota`, strict boot +
   `validate_startup`, Turnstile, `trust_proxy` + the Cloudflare-orange-cloud
   caveat, LLM error taxonomy, backups; **§4 measured sizing**, **§5 GCP
   free-tier runbook**, **§6 post-credit host options**.
   **Still to do:** `docs/DEVLOG.md` batch entry; `docs/SECURITY.md` (Turnstile
   threat model + fail-open rationale, error taxonomy + refund, boot guard, XFF
   trust model — now with the forged-header result to cite); `.env.example`
   check. Then stage the untracked `CLAUDE.md` + `context_files/` and commit the
   batch as **one unit**, gated on `pytest -q` green + `npm run build` clean.
   Push → first CI run (never verified — CI has never executed).
2. **The deploy itself** — target settled 2026-08-08: **GCP `e2-medium`**
   (2 vCPU / 4GB, Ubuntu 24.04, 30GB disk) on the $300/90-day trial credit, plus
   a free subdomain, running the §3 compose stack unchanged.
   *Oracle Always Free was the first choice and is out* — account signup itself
   was rejected (not just the known A1 capacity problem); revisit only if their
   support replies. AWS/Azure/GCP always-free VMs are all **1GB and cannot run
   this** (see sizing below). Note GCP `e2` is **x86_64** while the Phase 7
   images are `linux/arm64` — build on the VM, don't push from the laptop.
   Post-credit landing spot is Hetzner `CAX11` (~€4/mo, ARM, already proven).
   **Measured sizing (2026-08-08, `docker stats`)**: an api replica is 835MB idle
   but **1.83GB while answering** — it more than doubles. Whole stack: 2.2GB idle
   / **3.2GB peak** at 2 replicas, ~1.4GB / **~2.4GB** at 1. Floor is a 4GB box;
   `replicas: 1` is the free −835MB lever. `EURAG_RERANKER=none` would cut more
   but costs ~6pp phrase-hit *and* is a retrieval change (harness rule applies).
   API-cost policy (`EURAG_FREE_ANON_QUESTIONS`) deliberately parked by the user,
   not forgotten — it is one env var and the anon tier spends the *server's* key.
3. **The browser UI pass** — worth 10 minutes once something is deployed: the
   Turnstile widget was never actually rendered and clicked (curl covered every
   API path behind it).

After the batch, the "before real users" tier: accounts have no email (no
password reset), Google OAuth disabled, registry-uploads-per-instance, no
monitoring/streaming/i18n.

## Decisions / inputs waiting on you

**Blocking the deploy (nothing else is):**

1. **Create the GCP VM** — `e2-medium` (2 vCPU / 4GB), Ubuntu 24.04, **30GB**
   boot disk (the 10GB default is too small), tick *Allow HTTP/HTTPS traffic*,
   then **reserve a static external IP** (a stop/start otherwise changes the IP
   and breaks DNS). Full steps: [`docs/DEPLOY.md`](../docs/DEPLOY.md) §5.
2. **Pick the free subdomain** (DuckDNS or similar), A record → that static IP.
   Confirm `dig +short <subdomain>` before bringing the stack up, or Caddy's
   first cert attempt fails and it backs off. Sets `EURAG_DOMAIN`.
3. **Real Turnstile keys** (Cloudflare dashboard, registered for that subdomain)
   — the code is done and verified against the universal *test* keys, so going
   live is a pure env change (D6), no rebuild. **Note the test keys pass
   everything**, so deploying with them means no bot protection at all.

**Not blocking, decide later:**
- **Google OAuth client id/secret** — to enable the disabled "Continue with
  Google" button.
- **Monetization intent** — BYOK-only, or Stripe billing? This decides whether
  accounts need email + a payments path.
- **`EURAG_FREE_ANON_QUESTIONS`** — user explicitly **parked** the API-cost
  question on 2026-07-30 ("keep the API cost issue aside"). Not forgotten, and
  it matters before a public URL: the anon tier spends the *server's* key on
  full-quality, escalation-enabled answers. `0` = BYOK-only. One env var.
- **Industries answer** (long-open) — which sectors (food/textiles/machinery…)
  matter, for Tier-2 sector-law additions. The UI already logs each query's
  industry (`query industry context:`), so real usage can answer this too.

## Watch-outs (full list in CLAUDE.md → Gotchas)

- Chunker/schema changes are a silent no-op without `rm -rf var && python -m
  data.seed` (content-hash skip).
- `api/main.py` reads settings twice on purpose (import-time middleware vs.
  lifespan per-request) — don't collapse them.
- Local dev: `curl` and the browser share IP `127.0.0.1`, so curl-testing the
  anon endpoint spends the browser's free questions — reset with
  `rm var/eurag.sqlite3`.
- **Prod-compose secrets never go in `.env`** — `pydantic-settings` reads it and
  `EURAG_TURNSTILE_SECRET` there fails 13 tests locally (CI stays green). Use
  `--env-file .env.prod.local`.
- `EURAG_DOMAIN` needs the `:80` default at the **compose** layer; an empty-string
  env var makes Caddy refuse to parse the Caddyfile.
- On this machine Docker Desktop surfaced **two different client IPs** for
  host→Caddy traffic (the gateway `192.168.65.1` and the VPN egress address), so
  curl gets two independent anon quota buckets. Harmless, but it makes "3 free
  questions" look like more when smoke-testing — check the `anon_quota` table
  rather than counting requests.
