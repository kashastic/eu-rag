# HANDOFF — continue here

**As of:** 2026-08-09 · **EURAG is LIVE** at <https://eurag.duckdns.org> ·
tag `v1.0.0` · **237 tests pass, 6 skipped** locally.

Read [`CLAUDE.md`](../CLAUDE.md) first for standing rules — it wins on *how to
work here*; this file wins on *what is true right now*. Build history with
measurements: [`docs/DEVLOG.md`](../docs/DEVLOG.md). Why things are the way
they are: [`docs/UPDATE_LOG.md`](../docs/UPDATE_LOG.md).

---

## ⚠️ Start here: four finished changes are sitting uncommitted

Prod runs `1003058`. Everything below is **written, tested, and in the working
tree** — nothing is half-done, and **two of the four fix bugs production is
serving right now**. Suggested split is four commits, in this order (the user
must ask before you commit or push — standing rule 5):

```bash
git status --short
git log --oneline -1        # 1003058  (== origin/main == what prod runs)
.venv/bin/python -m pytest -q   # expect: 237 passed, 6 skipped
```

**1 · The language fix — deploy this one first; it is live-bug severity.**
The deployed commit added follow-up contextualisation, and that rewrite step can
**silently translate the question** — an English question was observed coming
back **answered in Spanish**.
Files: `core/retrieval/expansion.py`, `tests/unit/test_expansion.py`.
Verify after deploy: ask a DPO question, then `what if I have 29 people?` —
want a **GDPR Article 37 answer in English**.

*Why it happened, because it generalises:* `answerer`'s prompt promises *"write
the answer in the same language the question is written in"*. It kept that
promise — against the question **it was handed**, which was the contextualiser's
rewrite, not the user's words. Putting a rewrite in front of a component moves
that component's input-shaped promises onto the rewrite.

**2 · Anonymous free questions 3 → 2.** Default changed in `core/config.py`, and
the var is now passed through to the api container in `docker-compose.prod.yml`
— it wasn't before, so setting it in the VM's `.env` did nothing and the code
default silently won.
Files: `core/config.py`, `docker-compose.prod.yml`, `.env.example`.

**3 · Escalation telemetry + the uncited-refusal fix.** One unconditional
`query outcome:` line per query (the denominator the escalation rate never had),
plus `AnswerResult.insufficient_reason`; and `answer_question` now accepts an
honest zero-citation refusal, which used to cost 2 primary + 2 escalation calls
and shipped verbatim quotes from the chunks the model had just refused to use.
Files: `core/pipeline.py`, `core/generation/answerer.py`,
`tests/unit/test_answerer.py`, `tests/unit/test_query_telemetry.py` (new).

**4 · The web UI's bot gate — also live-bug severity, and frontend-only.**
Turnstile was rendered `appearance: "always"` on mount, so a **72px Cloudflare
checkbox sat above the composer on every anonymous page load**, and Ask /
Create-account were `disabled` until it solved. It now renders invisible
(`interaction-only` + `execution: "execute"`) and mints a fresh single-use token
**at submit time**; nothing is gated on the widget. Two bugs that fell out of
the old design and are fixed with it:

- with `challenges.cloudflare.com` unreachable (ad blocker, privacy extension,
  corporate proxy, **or a sitekey/domain mismatch**) both buttons were dead
  forever with **no error text at all**. They now fail with a message naming the
  blocked host.
- **`/login` could never create an account** — it was a pre-bot-gate copy of the
  form that posted `/auth/register` with no token and no widget, so it 403'd
  every time on any deploy with the gate on. It is now a redirect to
  `/chat?auth=login`; the modal is the one sign-in UI.

No backend change — the server's fail-closed policy on a missing token is
deliberately untouched. Files: `frontend/web/components/Turnstile.tsx`,
`frontend/web/app/chat/page.tsx`, `frontend/web/app/login/page.tsx`,
`frontend/web/app/globals.css`. Before/after measurements and the full
verification list: [`docs/DEVLOG.md`](../docs/DEVLOG.md).
Verify after deploy: load the site anonymously — **no checkbox above the
composer**, Ask clickable immediately, a question answers with citations; then
`/login` → create an account → you land signed in.

Deploy path for all four is the same one-liner in **The live deployment**
below. After deploying, the escalation rate finally becomes countable — see
**Measuring the escalation rate**.

Full write-ups: [`docs/UPDATE_LOG.md`](../docs/UPDATE_LOG.md) (why) and
[`docs/DEVLOG.md`](../docs/DEVLOG.md) (numbers).

---

## The live deployment

| | |
|---|---|
| URL | <https://eurag.duckdns.org> |
| Host | GCP `e2-medium` (2 vCPU / 4 GB), Ubuntu 24.04, europe-west3-b, `instance-20260808-155609` |
| IP | `34.141.28.118` (static, reserved as `eurag-ip`) · DuckDNS A record |
| Access | GCP console → SSH-in-browser. Repo at `~/eurag` |
| Cost | $0 — $300 / 90-day trial credit (started 2026-08-08) |
| Stack | `docker-compose.prod.yml`, `replicas: 1`, 2 GB swap |
| Secrets | `~/eurag/.env` **on the VM only** — never in the repo, never in the local `.env` |

**Update path** (~2 min build, seeder hash-skips all 47 documents in ~25s):

```bash
cd ~/eurag && git pull
docker compose -f docker-compose.prod.yml up --build -d
docker compose -f docker-compose.prod.yml logs -f seeder     # want "Seeded 47 documents"
curl -s https://eurag.duckdns.org/healthz | python3 -m json.tool
```

`healthz` must show `documents: 47`, `embedder: fastembed:…` (**never** `hash`),
`auth_enabled: true`, `encryption: true`, and a non-null `turnstile_sitekey`.

**Watching a request live** (the API logs are noisy; filter):

```bash
docker compose -f docker-compose.prod.yml logs -f api | grep -viE "healthz|httpx"
```

Look for `core.pipeline: contextualised follow-up: '…' -> '…'` and
`core.pipeline: low-confidence answer — escalating to …`.

**Measuring the escalation rate** — every query emits exactly one `query
outcome:` line (that is the denominator; every other per-query line fires only
on a branch). Do not use `--since` on the first look, and note that
`docker compose logs` only shows the **current** container, so a redeploy
resets the history:

```bash
total=$(docker compose -f docker-compose.prod.yml logs api | grep -c "query outcome:")
esc=$(docker compose -f docker-compose.prod.yml logs api | grep -c "escalated=True")
echo "$esc / $total escalated"
# and WHY they escalated: "marker" = a real corpus gap (what escalation is for),
# "uncited" = the model failed citation validation twice (a prompt problem)
docker compose -f docker-compose.prod.yml logs api \
  | grep -o "primary_reason=[a-z_]*" | sort | uniq -c
```

Nothing has been counted yet — the first attempt returned `0` over a `0`
denominator, i.e. no traffic in the window. **These numbers are still unknown**,
and they decide whether the relevance floor (open item 3) is worth building.

---

## What shipped today (2026-08-09)

**Deployed** — going live exposed three bugs no local rehearsal could have
found:

1. **`data/raw` bind-mount ownership** — gitignored, so absent from a fresh
   clone; Docker created it `root:root` and the container runs as uid 10001.
   Seeder died in 5s. Needs `chown -R 10001:10001 data/raw` before the first
   `up` on Linux. **Invisible on macOS** — Docker Desktop remaps ownership.
2. **Unbounded cross-encoder batch → OOM.** fastembed defaults to
   `batch_size=64`, above every pool the retriever builds (60 on the escalation
   path), so one rerank allocated **1.6 GB** and the OOM killer took the API
   container — `502` with no traceback. Fixed by `EURAG_RERANK_BATCH` (default
   8): 284 MB, same scores.
3. **Follow-ups retrieved on a fragment** — `pipeline.query()` took no history,
   so "what if I have 29 people?" hit the Pay Transparency Directive. Fixed by
   `QueryContextualizer`. Harness: doc_hit 94%→100%, phrase_hit 87%→94%.

**Written but not yet committed or deployed** — the three changes at the top of
this file:

4. **The contextualiser's language leak** (the fix for #3's own regression).
5. **Anonymous free questions 3 → 2**, plus the compose passthrough that makes
   the env var actually reach the container.
6. **Escalation telemetry, and the uncited-refusal fix it exposed.** Started as
   "how do we escalate fewer questions?" and found the rate was unmeasurable —
   every per-query log line fired on a branch, so there was no denominator.
   Fixing that surfaced the real bug: an honest zero-citation refusal, which is
   what `SYSTEM_PROMPT` asks for, failed citation validation twice and escalated
   into verbatim quotes from the chunks it had just refused to use. 4 LLM calls
   → 2, and the refusal itself now ships.
7. **The web UI's bot gate.** The always-visible Turnstile checkbox is gone
   (invisible widget, executed at submit), no button is gated on it any more,
   and the two sign-in failures that came with the old design — a permanently
   dead UI when `challenges.cloudflare.com` is blocked, and a `/login` page that
   could never register — are fixed. Measured with a real browser; numbers in
   [`docs/DEVLOG.md`](../docs/DEVLOG.md).

---

## Open work, in priority order

1. **Ship the three uncommitted changes** (top of this file). The language fix
   is the urgent one — prod is serving that bug now.
2. **Billing alerts — not set.** A public URL spends the server's Anthropic key
   on anonymous full-quality, escalation-enabled answers. Set a spend limit on
   the key and GCP alerts at $50/$150. `docs/DEPLOY.md` §5.6.
3. **Cut the escalation rate — measure first, then fix.** The `query outcome:`
   telemetry shipped for exactly this; give it real traffic before touching
   anything, then work the `primary_reason` histogram:

   - ~~**`uncited` → a bug**~~ **— fixed 2026-08-09.** An honest zero-citation
     refusal was rejected by `validate_answer`, retried, downgraded to
     extractive quotes and then escalated (2 + 2 calls). `answer_question` now
     accepts an uncited answer when the insufficiency marker is present and the
     text is ≤600 chars — 1 + 1 calls, and the model's own refusal ships. **The
     escalation still fires**; `primary_reason` just reads `marker` instead of
     `uncited`. A high `uncited` count from here on is a genuine
     citation-formatting problem, not this bug.
   - **No relevance floor** — the remaining lever on escalation *count*, and
     now the top one. RRF ranks by relative position and the
     cross-encoder only reorders — nothing checks whether the best chunk is
     actually relevant, so `mode="no_sources"` never fires on a 47-doc corpus
     and every off-corpus question rides the full cascade. A score threshold
     that returns `NO_SOURCES_MESSAGE` with **zero** LLM calls is the biggest
     cost lever on the anonymous tier. Needs threshold calibration.
   - **Split the escalation triggers.** `pipeline.query` gates on
     `insufficient`, which conflates "corpus doesn't cover this" (wants deeper
     retrieval) with "citation validation failed" (wants a better prompt).
     `insufficient_reason` already carries the distinction; the gate just
     doesn't read it yet.
   - **The two known phrase-precision misses escalate every single time** —
     GDPR Art. 6 lawful-bases and Late Payment statutory-interest retrieve the
     right doc but an adjacent slice, so each is a standing Opus tax. The first
     pass caps 2 chunks/doc and escalation's trick is `max_per_doc=6`; try 3 on
     the first pass. **Retrieval change → standing rule 1**: harness before/
     after, pinned with `EURAG_HYDE_MODEL=none`.

4. **Move the seed lock out of the bind mount** — `data/raw/.seed.lock` →
   `/app/var/.seed.lock`. The `apivar` named volume is already owned by
   `eurag:eurag` and shared across replicas, so the flock still works and the
   manual `chown` disappears from every future Linux deploy.
5. **Re-measure sizing on Linux.** `docs/DEPLOY.md` §4's memory numbers were
   measured on macOS/arm64 and are flagged as extrapolated. Capture
   `docker stats --no-stream eurag-api-1` during an escalated query (the Late
   Payment statutory-interest question reliably escalates) and replace them.
6. **90-day credit cliff** — trial started 2026-08-08. Landing spot is Hetzner
   `CAX21` (~€7/mo, ARM — and the original images were built arm64).
   `docs/DEPLOY.md` §6.

**Deferred:** accounts have no email → **no password reset** (lose the password,
lose the saved chats — this is the main gap before real users), Google OAuth
disabled, registry-uploads-per-instance, no streaming, no i18n, no monitoring.

## Decisions waiting on the user

- ~~**`EURAG_FREE_ANON_QUESTIONS`**~~ — **decided 2026-08-09: 2.** Default is now
  `2` in `core/config.py` and passed through to the api container in
  `docker-compose.prod.yml` (it wasn't before, so the VM's `.env` couldn't
  actually change it). Takes effect on the next redeploy. Anonymous answers are
  still full-quality *and* escalation-enabled on the server's key, so a spend
  limit is still the control that matters — see open item 2. `0` = BYOK-only.
- **Monetization** — BYOK-only or Stripe? Decides whether accounts need email.
- **Google OAuth client id/secret** — to enable the disabled button.
- **Industries** (long-open) — which sectors matter for Tier-2 sector law. The
  UI logs each query's industry (`query industry context:`), so usage can answer
  this — but **how much real traffic exists is itself unconfirmed** (the first
  log count came back empty), and that line only fires when an industry is set.
  Check the `query outcome:` count first; it is the only reliable denominator.

---

## Traps that will cost you an hour (full list: CLAUDE.md → Gotchas)

- **Pin `EURAG_HYDE_MODEL=none` before any retrieval A/B.** Expansion calls
  Haiku, so the harness is non-deterministic; the golden set has 3 compound
  cases, so one flip is 33pp. A "safer" rerank batch was nearly shipped on that
  noise.
- **A per-query rate needs an unconditional log line.** Every other per-query
  line fires on a branch, so a count of escalations had nothing to divide by —
  `0` results were uninterpretable (no traffic? wiped logs? bad pattern?).
  `query outcome:` is the denominator. Keep grep-targeted strings **ASCII**: one
  of the failed counts was grepping the **em dash** in `low-confidence answer —
  escalating to …`.
- **Don't re-tighten citation validation to always require a citation.** An
  honest refusal is allowed to cite nothing (marker present, ≤600 chars). The
  old unconditional rule rejected the model for obeying its own prompt and cost
  4 LLM calls per refusal. Fabricated markers and long uncited bodies are still
  rejected — that is what the two guards in `answer_question` are for.
- **`import core.config` first in any ad-hoc LLM script.** It loads `.env` at
  import time. Without it you silently get `ExtractiveClient`, every LLM helper
  returns its fallback, and a working change looks completely broken. **Check
  `type(llm).__name__` before believing a result.**
- **Never put prod secrets in the local `.env`.** `pydantic-settings` reads it,
  and `EURAG_TURNSTILE_SECRET` there turns the bot gate on inside the test suite
  — 13 failures locally while CI stays green.
- **Chunker or registry-schema changes are a silent no-op** without
  `rm -rf var && python -m data.seed` (content-hash skip).
- **`api/main.py` reads settings twice on purpose** — don't collapse them.
- **Local dev shares `127.0.0.1`** between curl and the browser, so curl-testing
  the anon endpoint spends the browser's free questions.
- **A rewrite step inherits the promises of whatever it feeds** — see the bug at
  the top of this file.
- **Never gate a submit button on a third-party widget solving.** Ask for the
  token at submit time and show an error if you can't get one. The old
  Turnstile wiring left Ask *and* Create-account permanently `disabled` with no
  message whenever the challenge couldn't run — and a sitekey/domain mismatch
  is the most likely thing to go wrong on a fresh deploy.
- **The bot gate is invisible locally.** No `EURAG_TURNSTILE_SECRET` means the
  server skips verification entirely, so a client that sends no token passes.
  That is precisely how `/login` shipped unable to register: broken only where
  the gate is configured, i.e. only in prod. Rehearse auth changes with
  Cloudflare's test keys — sitekey `1x00000000000000000000AA` (always passes) or
  `3x00000000000000000000FF` (forces an interactive challenge), secret
  `1x0000000000000000000000000000000AA` — passed on the command line, **never
  in `.env`** (that breaks 13 tests; see above).
