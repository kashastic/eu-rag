# EURAG — Make it live safely (blockers + hardening batch)

## Context

EURAG v1.0.0 (M1–M6, 163 tests green, tagged) is feature-complete but not safely
deployable. Verified this session: a clean build seeds **4 docs instead of 47**
(`data/raw/` gitignored, cache-only loaders silently skip); the anonymous tier
exposes Opus-capable calls with **no bot protection**; missing shared secrets
only log instead of refusing to boot; **the prod web image cannot even build**
(`frontend/web/Dockerfile:13` copies a nonexistent `public/`); both API replicas
**re-seed the corpus on every fresh container** (no shared `/app/var` volume) and
a cold seed exceeds the 40s healthcheck start-period; LLM failures surface as
raw 500s and **burn an anon free question** (consume-before-call, no refund);
and there is no CI.

User decisions (locked): scope = blockers + hardening; deploy target = **real
VPS + domain** (Caddy auto-HTTPS via `EURAG_DOMAIN`); Turnstile built now
against **Cloudflare's universal test keys** so real keys are a deploy-time env
change only.

**Explicitly deferred** (documented non-blockers): registry→Postgres port
(uploads-only boundary, `docs/DEPLOY.md:43-53`), email/password reset, Google
OAuth, billing, streaming, i18n, monitoring.

## Design decisions (the load-bearing ones)

| # | Decision | Choice |
|---|---|---|
| D1 | Corpus | **Scrape-at-first-boot via one-shot `seeder` compose service**; document "rsync your populated `data/raw/` to the VPS" as the fast path. No 14MB of third-party HTML in git. |
| D2 | Serialization | `seeder` runs once; `api` gets `depends_on: seeder: condition: service_completed_successfully`. `fcntl.flock` on `data/raw/.seed.lock` inside `seed.py` as insurance for `--scale api=N`. |
| D3 | Seeded state | Shared named volumes: `apivar:/app/var` (seeder + both replicas — registry visible everywhere) and `modelcache:/home/eurag/.cache` (ONNX models download once). Requires WAL + `busy_timeout` PRAGMAs on the Registry connection. |
| D4 | Fail-loud prod | `EURAG_STRICT_BOOT=true` in prod compose: embedder fastembed-fallback **raises** instead of silently degrading to hash (garbage-vectors trap), seed failure is fatal. Default off → local zero-config untouched. |
| D5 | Funding-doc churn | Stamp snapshot date from `calls.json` mtime, not `date.today()` → content hash stable across reseeds. |
| D6 | Sitekey delivery | **Serve `turnstile_sitekey` from `/healthz` at runtime** (chat page already calls `api.health()` on init — verified `chat/page.tsx:45`). No `NEXT_PUBLIC_*` build-arg, no web-image rebuild to rotate keys. |
| D7 | siteverify failure | Fail-**open** on network error (per-IP quota still holds; CF outage must not kill the anon funnel); fail-**closed** on explicit `success:false` or missing token. |
| D8 | HTTP client | stdlib `urllib` (3s timeout) with a monkeypatchable `_post` seam — matches existing scraper pattern; httpx stays dev-only. |
| D9 | Register too | Turnstile also on `/auth/register` — bot signups bypass the anon quota and get server-key Haiku. |
| D10 | Static UI | Skipped explicitly: local-mode-only in prod (Caddy routes `/` to web) and its CSP blocks the CF script anyway. State in SECURITY.md. |
| D11 | Quota fairness | **Consume-then-refund** on `LLMUnavailableError` (new `AnonQuota.refund`). Consume-on-success would reopen the parallel-request overrun. |
| D12 | Bad BYOK status | **400 `byok_key_rejected`**, not 401 — web client treats 401 as "refresh session token". |
| D13 | Healthcheck | `--start-period=180s --interval=15s --timeout=5s --retries=5` (API boot no longer seeds, but first Pipeline init loads ~200MB ONNX). |
| D14 | Ingest caps | Constants, no knob: `text ≤ 500_000`, `source_url ≤ 2000`, `source_type ≤ 40`, `language ≤ 16`. |
| D15 | Limiter XFF | `EURAG_TRUST_PROXY` (default false; prod true): rate limiter keys on first-hop XFF — today all visitors behind Caddy share ONE bucket (`request.client.host` = Caddy). Safe: Caddy ≥2.5 strips client-supplied XFF. |
| D16 | CI PG job | Include postgres:16 service job — the 2 parity tests are the only guard on the dialect layer while the registry port is deferred. |
| D17 | Secret guard | `validate_startup(settings, db_url)` in `core/config.py`, called in `api/main.py` right after `_settings` (import time; `database_url()` is a plain env read). Raise on auth+PG+no-jwt-secret; warn on missing encryption key. Directly testable — no `importlib.reload`. |

**New config** (`core/config.py`): `turnstile_secret` (`EURAG_TURNSTILE_SECRET`,
None=off), `turnstile_sitekey` (`EURAG_TURNSTILE_SITEKEY`, None=no widget),
`strict_boot` (`EURAG_STRICT_BOOT`, false), `trust_proxy` (`EURAG_TRUST_PROXY`,
false). Compose: `EURAG_EXPECT_DOCS` (47).

## Phases

Order: corpus first (a deploy is hollow without it); Turnstile before the
LLM-error work (both edit `query.py`'s anon branch — token check inserts
*before* `consume` at line 37, refund wraps *after*); CI after all behavior
changes; deploy is the integration test; docs+commit last.

### Phase 1 — Corpus reproducibility
- `data/seed.py`: argparse `main()` — `--scrape` (fill missing caches via new
  fetch-only helpers in the scrapers; EUR-Lex keeps its 10s throttle, portals
  default selection, `funding_calls.fetch_calls()`), `--expect-docs N` (exit 1
  below N). `fcntl.flock` on `data/raw/.seed.lock`. `seed(pipeline)` signature
  unchanged (conftest depends on it).
- `data/scrapers/eurlex.py`, `portals.py`: expose ensure-cache entry points
  (refactor of existing main loops, no CLI change).
- `data/scrapers/funding_calls.py`: `document_from_calls(data, snapshot_date=None)`;
  cached path passes cache-file mtime date (D5).
- `core/ingestion/embedder.py`: `get_embedder(..., strict=False)` raises on
  fastembed failure when strict; `core/pipeline.py:26` passes
  `settings.strict_boot` (D4). `core/config.py`: add `strict_boot`.
- `core/registry.py` (~line 54): add `PRAGMA journal_mode=WAL`,
  `PRAGMA busy_timeout=5000` (D3 prerequisite; connection settings, not schema
  — no reseed).
- `docker-entrypoint.sh`: seed failure fatal under strict boot, lenient otherwise.
- `Dockerfile`: HEALTHCHECK per D13.
- `docker-compose.prod.yml`: add `seeder` one-shot service (`python -m data.seed
  --scrape --expect-docs ${EURAG_EXPECT_DOCS:-47}`, strict, `restart: "no"`,
  depends on qdrant); `api` gains `apivar` + `modelcache` volumes, strict boot,
  `depends_on: seeder: service_completed_successfully`; declare both volumes.
- Tests: `--expect-docs` exit-1; funding hash stable across reloads and
  independent of today; embedder strict-raise vs lenient-fallback.
- Verify: `pytest -q`; `python -m data.seed --expect-docs 47` → exit 0;
  `docker compose -f docker-compose.prod.yml build` succeeds.

### Phase 2 — Turnstile at the anonymous boundary
- `core/security/turnstile.py` (new): `verify(token, secret, remoteip=None,
  timeout=3.0) -> bool` — missing token → False (no network); urllib POST to
  siteverify via module-level `_post` seam; network failure → True + warning
  (D7/D8).
- `api/routes/query.py`: `QueryRequest` + `turnstile_token: str | None`
  (max_length 2048); anon branch **before** `consume` (line 37): if
  `settings.turnstile_secret` and not verified → 403 `turnstile_failed`.
  (Anon branch only reachable when auth on — gate on secret alone suffices.)
- `api/routes/auth.py`: register accepts optional `turnstile_token`
  (new `RegisterRequest`; login untouched), same check (D9).
- `api/main.py`: `/healthz` adds `turnstile_sitekey` (D6).
- `frontend/web/components/Turnstile.tsx` (new): explicit-render widget,
  injects the CF script once, onToken/expired/error callbacks, exposes reset.
- `frontend/web/app/chat/page.tsx`: sitekey from health in `init` (line 44-61);
  widget in anon composer (~257); token threaded into `send()` anon branch
  (119-137); **widget reset after every question** (tokens single-use); Ask
  gated on token when sitekey present ∧ !authed; register modal gets widget.
- `frontend/web/lib/api.ts`: `queryAnon(q, industry?, turnstileToken?)`,
  `register(..., turnstileToken?)`, health type + `turnstile_sitekey`.
- `.env.example` + prod compose api env: the two Turnstile vars. **No
  layout.tsx / web Dockerfile / build-args changes.** Static UI skipped (D10).
- Tests: unit verify() matrix (monkeypatch `_post`); API fixture à la
  `test_tiers.py:33-42` + secret + monkeypatched verify — missing/bad token →
  403 **and quota untouched**; valid → 200; register w/o token → 403. Existing
  suites unmodified (no secret → off).
- Verify: `pytest -q`; manual with universal test keys — always-pass pair
  (`1x…AA`/`1x…AA`) answers flow; always-fail secret (`2x…`) → 403, no quota burn.

### Phase 3 — Startup secret guard
- `core/config.py`: `validate_startup(settings, db_url)` (D17).
- `api/main.py`: call it right after line 34 (`database_url` already imported).
  Dual settings reads untouched (CLAUDE.md gotcha).
- Tests (`tests/unit/test_boot_guard.py`): raise / ok / warn matrix on the
  helper directly, `caplog` for the warning.
- Verify: `pytest -q`; `EURAG_AUTH_ENABLED=true EURAG_DATABASE_URL=postgresql://x/x
  python -c "import api.main"` → RuntimeError.

### Phase 4 — LLM error handling + quota fairness
- `core/generation/llm_client.py`: `LLMUnavailableError(kind)` raised from
  `AnthropicClient.complete` (lines 30-37): AuthenticationError/
  PermissionDenied→`auth`, RateLimit→`rate_limited`, Overloaded/InternalServer→
  `overloaded`, APIConnection/Timeout→`network`, other APIStatus→`upstream`.
- `core/pipeline.py`: escalation best-effort — wrap second `_answer` (line 177)
  in try/except → keep primary answer. **Fix line 172 bug**: use request-scoped
  `escalation.name`, not `self.escalation_llm.name` (None on BYOK-only → crash).
- `core/retrieval/expansion.py`: no change (broad except already degrades HyDE
  to raw query — verified).
- `api/main.py`: `@app.exception_handler(LLMUnavailableError)` — `auth` → 400
  `byok_key_rejected` ("Your Anthropic API key was rejected — update or remove
  it in Settings."); others → 503 `llm_unavailable` + `Retry-After: 10` (D12).
  Covers /query and conversations in one place.
- `core/quota.py`: `refund(key)` — `UPDATE … SET used = used - 1 WHERE … AND
  used > 0` in its own transaction (D11).
- `api/routes/query.py`: anon branch wraps `pipeline.query` → on
  `LLMUnavailableError` refund then re-raise.
- Tests: SDK-exception mapping (anthropic exceptions constructible offline with
  httpx Response); RaisingLLM; escalation failure preserves primary; line-172
  regression; route-level 400/503; anon remaining unchanged after failed call;
  refund-at-0 no-op.

### Phase 5 — Hardening odds & ends
- `api/routes/ingest.py:14-17`: Field caps (D14); oversize → 422 test.
- `core/quota.py` consume: opportunistic `DELETE WHERE day < today-2` in the
  existing transaction (ISO string compare works both dialects); test.
- `frontend/web/public/.gitkeep` (new) — unbreaks the prod web image build.
- `.gitignore`: `.env.*` + `!.env.example`.
- Rate limiter: `trust_proxy` per D15 — `RateLimiter(..., trust_proxy)`, `_key`
  uses first-hop XFF for anon when set; prod compose `EURAG_TRUST_PROXY=true`;
  standalone-app test (distinct XFF ⇒ distinct buckets iff trusted).
- Verify: `pytest -q`; `cd frontend/web && npm run build`; web image builds.

### Phase 6 — CI (`.github/workflows/ci.yml`, new)
- **python**: ubuntu, py3.11 (prod parity), pip cache, `pip install -e ".[dev]"`,
  `pytest -q` (offline by design).
- **web**: node 22, npm cache on `frontend/web/package-lock.json`,
  `npm ci && npm run build`.
- **postgres** (D16): postgres:16 service, `.[dev,prod]`,
  `EURAG_TEST_DATABASE_URL=… pytest tests/test_postgres.py -q`.

### Phase 7 — Prod bring-up (local) + VPS deploy
Local rehearsal (macOS: `open -a Docker` first):
1. `.env`: POSTGRES_PASSWORD, JWT + encryption keys (`openssl rand -hex 32`),
   ANTHROPIC_API_KEY, Turnstile **test** pair, EURAG_DOMAIN unset (`:80`).
2. `docker compose -f docker-compose.prod.yml up --build` — seeder logs 47 docs
   exit 0 (local `data/raw` populated → no scraping); replicas healthy < 180s.
3. Smoke: `/healthz` documents=47, embedder=fastembed, turnstile_sitekey set →
   browser: anon Q (widget auto-pass) → cited answer → 3 Qs → wall → register
   (widget) → free banner → BYOK → `tier: byok` → saved chat survives reload →
   bad BYOK key → friendly 400 → 11 rapid queries → 429.
4. Resilience: `docker kill` one api container → still answering.

VPS runbook (→ DEPLOY.md):
1. VM ≥ 2 vCPU/4GB, Ubuntu 24.04, docker + compose plugin, ports 80/443, DNS A
   record.
2. `git clone`; fast path `rsync -a data/raw/ vps:…/data/raw/` (skips ~6min
   scrape) or let the seeder scrape on first boot.
3. `.env` with real secrets + **real Turnstile keys** + `EURAG_DOMAIN` — env
   change only (D6).
4. `up --build -d`; seeder → 47 docs; Caddy provisions cert.
5. Smoke over HTTPS (checklist above with real widget).
6. Backups: daily `pg_dump | gzip` cron + off-host copy; volumes pgdata/qdrant/
   apivar.
7. Updates: `git pull && up --build -d` — hash-skips mean no re-embed (D5).

### Phase 8 — Docs + commit
- DEVLOG batch entry (decisions + verification results); DEPLOY.md rewrite
  (seeder, shared volumes, strict boot, Turnstile, trust_proxy, runbook,
  backups); SECURITY.md (Turnstile threat model + fail-open rationale, error
  taxonomy + refund, boot guard, XFF trust model); `.env.example` new vars;
  CLAUDE.md gotchas update; stage untracked `CLAUDE.md` + `context_files/`.
- Final gate: `pytest -q` green + `npm run build` clean → commit (ends
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`), push → first CI run
  green.

## Constraint audit
Local zero-config mode untouched (every new flag defaults off/None); offline
suite stays offline (all Turnstile/LLM tests monkeypatched); `api/main.py` dual
settings reads preserved; no chunker/registry schema change (PRAGMAs are
connection settings — no reseed); **no retrieval changes** → no harness run
required, per standing rule.

## Verification (end-to-end)
1. `.venv/bin/python -m pytest -q` — green after every phase.
2. `python -m data.seed --expect-docs 47` → exit 0 locally.
3. Fresh-clone simulation: temp dir clone → `data.seed --scrape` path exercised.
4. `docker compose -f docker-compose.prod.yml up --build` → full smoke checklist.
5. `python -m infra.scripts.check_links` (citations resolve, standing rule 2).
6. GitHub Actions: 3 jobs green on push.

## Inputs needed from user (at deploy time, not before)
- Real Cloudflare Turnstile site + secret keys (domain-registered).
- VPS credentials/hostname + domain for `EURAG_DOMAIN`.
- `ANTHROPIC_API_KEY` for the server.
