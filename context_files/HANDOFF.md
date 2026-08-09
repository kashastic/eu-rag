# HANDOFF — continue here

**As of:** 2026-08-09 · **EURAG is LIVE** at <https://eurag.duckdns.org> ·
tag `v1.0.0` · **220 tests pass, 6 skipped**. For build history read
[`docs/DEVLOG.md`](../docs/DEVLOG.md); for why things are the way they are read
[`docs/UPDATE_LOG.md`](../docs/UPDATE_LOG.md); for standing guidance read
[`CLAUDE.md`](../CLAUDE.md) — CLAUDE.md wins on *standing* rules, this file wins
on *current state*.

### Read this first (state in 6 lines)

- **It is deployed and serving.** GCP `e2-medium` (2 vCPU / 4 GB, europe-west3,
  static IP `34.141.28.118`), Docker Compose prod stack, real Turnstile keys,
  Let's Encrypt cert, 47 documents.
- **`origin/main` = `f54d1f2`**, which is what the VM is running.
- **Two fixes are built and green but NOT committed and NOT deployed** — see
  "What's in the working tree" below. Production still has both bugs.
- **CI is green** — first run ever passed all three jobs.
- **Do not commit or push unless asked** (standing rule 5).
- The live deploy is done, so the old "blocked on inputs" section is gone; what
  remains is in "Decisions waiting on you".

## The live deployment

| | |
|---|---|
| URL | <https://eurag.duckdns.org> (DuckDNS A record → `34.141.28.118`) |
| Host | GCP `e2-medium`, Ubuntu 24.04, 30 GB disk, `instance-20260808-155609` |
| Cost | $0 — $300 / 90-day trial credit. Post-credit target: Hetzner `CAX21` (~€7/mo) |
| Stack | `docker-compose.prod.yml`, `replicas: 1`, 2 GB swap added |
| Secrets | `~/eurag/.env` on the VM (never in the repo, never in the local `.env`) |

**Deploying an update:**

```bash
cd ~/eurag && git pull
docker compose -f docker-compose.prod.yml up --build -d
docker compose -f docker-compose.prod.yml logs -f seeder   # ~4s, 47 hash-skipped
curl -s https://eurag.duckdns.org/healthz | python3 -m json.tool
```

**Three prod-only bugs were found by deploying**, none catchable in the macOS
rehearsal — the `caddy` env block, the `EURAG_DOMAIN` empty-string default, and
the `data/raw` bind-mount ownership. All fixed; details in the DEVLOG.

## What's in the working tree (uncommitted)

Both are fully built, tested, and documented. Neither is live.

**1. Reranker OOM fix.** `EURAG_RERANK_BATCH` (default 8). fastembed's default
of 64 exceeded every pool the retriever builds, so a 60-candidate escalation
pool went through in one forward pass and allocated **1.6 GB** — which
OOM-killed the api container in production (502s, no traceback). Now 284 MB.
Harness: identical metrics from batch 4 to 64.

**2. Follow-up contextualisation (Bug B).** `QueryContextualizer` rewrites a
follow-up into a standalone question before retrieval. Fixes "what if I have 29
people?" retrieving the Pay Transparency Directive. Harness: doc_hit 94%→100%,
phrase_hit 87%→94%, follow-up cases 2-of-3-MISS → 3-of-3 rank 1.

**Verification after deploying both:**

- **Late Payment statutory-interest question** — reliably escalates, so it
  exercises the exact path that OOM-killed the container. Want `★ escalated`
  and a cited answer, not a 502.
- **DPO → "what if I have 29 people?"** — want a GDPR Article 37 answer, not
  Pay Transparency.
- `docker stats --no-stream eurag-api-1` during the escalated query — the
  memory numbers in `docs/DEPLOY.md` §4 were measured on macOS/arm64 and are
  extrapolated for Linux/x86_64. Worth replacing with real ones.

## Resume in 60 seconds

```bash
cd /Users/akashacharya/Claude_Arena/EU_RAG && source .venv/bin/activate
.venv/bin/python -m pytest -q          # expect: 220 passed, 6 skipped
git status --short                     # expect: the two uncommitted fixes
curl -s https://eurag.duckdns.org/healthz | python3 -m json.tool

# retrieval quality — ALWAYS pin expansion, or the numbers are noise:
EURAG_HYDE_MODEL=none python -m core.evaluation.harness
```

## Known open work

1. **Deploy the two fixes** (above) — production still has both bugs. It is
   stable for normal single questions; escalated questions risk a 502.
2. **Move the seed lock out of the bind mount.** `data/raw/.seed.lock` →
   `/app/var/.seed.lock` (the `apivar` named volume is already owned by
   `eurag:eurag` and shared across replicas). Removes the manual
   `chown -R 10001:10001 data/raw` from every future Linux deploy.
3. **Billing alerts.** Not set. A public URL spends the server's Anthropic key
   on anonymous full-quality answers. Set a spend limit on the key and GCP
   alerts at $50/$150 — `docs/DEPLOY.md` §5.6.
4. **Re-measure sizing on Linux** — see above.
5. **The 90-day credit cliff.** Trial started 2026-08-08. `docs/DEPLOY.md` §6
   has the post-credit options.

Deferred beyond that: accounts have no email (no password reset), Google OAuth
disabled, registry-uploads-per-instance, no streaming/i18n/monitoring.

## Decisions waiting on you

- **`EURAG_FREE_ANON_QUESTIONS`** — still 3, now on a public URL. The exposure
  is real (anonymous answers are full-quality *and* escalation-enabled, billed
  to the server's key); the recommended control is a spend limit rather than a
  smaller number. `0` makes it BYOK-only. One env var.
- **Monetization** — BYOK-only, or Stripe? Decides whether accounts need email
  and a payments path.
- **Google OAuth client id/secret** — to enable the disabled button.
- **Industries** (long-open) — which sectors matter for Tier-2 sector law. The
  UI logs each query's industry (`query industry context:`), so live usage can
  answer this now that there is live usage.

## Watch-outs (full list in CLAUDE.md → Gotchas)

- **Pin `EURAG_HYDE_MODEL=none` before any retrieval A/B** — expansion calls
  Haiku, so the harness is non-deterministic and a one-case delta (33pp on
  compound_hit) is noise, not signal.
- **Prod secrets never go in the local `.env`** — `pydantic-settings` reads it
  and `EURAG_TURNSTILE_SECRET` there fails 13 tests locally while CI stays green.
- A Linux deploy needs `chown -R 10001:10001 data/raw` before the first `up`
  (until open item 2 is done).
- Chunker/schema changes are a silent no-op without `rm -rf var && python -m
  data.seed`.
- `api/main.py` reads settings twice on purpose — don't collapse them.
- Local dev: `curl` and the browser share `127.0.0.1`, so curl-testing the anon
  endpoint spends the browser's free questions.
