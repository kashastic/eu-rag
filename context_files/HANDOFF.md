# HANDOFF — continue here

**As of:** 2026-08-10 · **EURAG is LIVE** at <https://eurag.duckdns.org> ·
prod runs `15cbfc0` · **307 tests pass, 7 skipped** locally.

> **Uncommitted and not yet deployed:** the business-context batch (intro-screen
> profile — country / size / sector / AI role). Backend, frontend, tests, and
> `/privacy` are done and verified locally; nothing is on prod. See *What
> shipped 2026-08-10 (business context)* below and `docs/DEVLOG.md` for the
> numbers.

Read [`CLAUDE.md`](../CLAUDE.md) first for standing rules — it wins on *how to
work here*; this file wins on *what is true right now*. Build history with
measurements: [`docs/DEVLOG.md`](../docs/DEVLOG.md). Why things are the way
they are: [`docs/UPDATE_LOG.md`](../docs/UPDATE_LOG.md).

**The working tree is clean and everything is deployed.** Start by confirming
that, not by looking for pending work:

```bash
git status --short && git log --oneline -1     # expect: clean, 15cbfc0
.venv/bin/python -m pytest -q                  # expect: 277 passed, 7 skipped
curl -s https://eurag.duckdns.org/healthz | python3 -m json.tool
```

**The privacy claims are checkable from here** — re-run these if you touch the
frontend, because they are what `/privacy` promises in writing:

```bash
# must print NOTHING: no third-party host in the served HTML
curl -s -L https://eurag.duckdns.org/chat | grep -oE "https://[a-z0-9.-]+\.(com|net)" | sort -u
# fonts must come from our own origin (200 font/woff2)
curl -s -o /dev/null -w "%{http_code} %{content_type}\n" \
  https://eurag.duckdns.org/fonts/fraunces-italic-latin-ext.woff2
```

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
`auth_enabled: true`, `encryption: true`, a non-null `turnstile_sitekey`, and a
non-null `google_client_id`.

> **`0 documents indexed` in the UI means `/healthz` is failing** — not that the
> corpus is empty. The web app reads `documents`, `turnstile_sitekey` *and*
> `google_client_id` from that one endpoint, so a dead healthz blanks all of
> them at once and the Google button silently disappears. If `curl … | json.tool`
> says *"Expecting value: line 1 column 1"* you are looking at Caddy's error
> page: the API never started. `docker compose -f docker-compose.prod.yml logs
> api | head -40` will have the traceback. This exact thing happened on
> 2026-08-10 (a migration bug, see UPDATE_LOG).

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

**Still uncounted.** The first attempt returned `0` over a `0` denominator — no
traffic in the window — and every redeploy since has reset the log. These
numbers decide whether the relevance floor (open item 2) is worth building, so
take the reading before touching retrieval.

---

## What shipped 2026-08-09 → 08-10

All live. Eleven commits, `1003058..7392001`. Reasoning in
[`docs/UPDATE_LOG.md`](../docs/UPDATE_LOG.md), numbers in
[`docs/DEVLOG.md`](../docs/DEVLOG.md).

**Bugs production was serving**

1. **The contextualiser translated the question** — an English question came
   back answered in Spanish. A rewrite step inherits the promises of whatever it
   feeds; `answerer` promises to answer in the question's language and kept that
   promise against the *rewrite*.
2. **The bot gate was the front door.** A 73px Cloudflare checkbox sat above the
   composer on every anonymous page load, and Ask / Create-account were disabled
   until it solved — so a blocked `challenges.cloudflare.com` (ad blocker,
   privacy extension, **or a sitekey/domain mismatch**) left both buttons dead
   with no error at all. Now invisible (`interaction-only` + `execution:
   "execute"`), executed at submit, nothing gated on it.
3. **`/login` could never create an account** — a pre-bot-gate copy of the form
   that posted `/auth/register` with no token and no widget. Now a redirect to
   `/chat?auth=login`; the modal is the one sign-in UI.
4. **Every field-level 422 rendered as nothing.** FastAPI returns validation
   errors as an *array* in `detail`; the client parsed only string and
   `{code,message}`, so `err.message` was `""` and `{error && …}` drew nothing.
   Reported as "nothing happens when I click Create account".

**New**

5. **Anonymous free questions 3 → 2**, plus the compose passthrough that makes
   `EURAG_FREE_ANON_QUESTIONS` actually reach the container (it never had).
6. **Escalation telemetry** — one unconditional `query outcome:` line per query,
   plus `AnswerResult.insufficient_reason`. Fixing the measurement exposed the
   bug: an honest zero-citation refusal failed citation validation twice and
   escalated into verbatim quotes from the chunks it had just refused. 4 LLM
   calls → 2.
7. **The logged-in free tier is now capped** — `EURAG_FREE_USER_QUESTIONS`
   (default 10) for the **lifetime of the account**, then BYOK. This was the last
   uncapped path to the owner's Anthropic bill.
8. **BYOK honesty pass** — the dialog now states that the operator can
   technically read a stored key, tells the user to use a dedicated key with a
   spend limit, notes that removing it here does not revoke it at Anthropic, and
   nudges rotation past 30 days.
9. **Google sign-in works** (ID-token flow, no client secret). See below.
10. **Answer badges read like English** — `★ escalated` → `★ stronger model
    consulted`; `mode: llm` gone (it was on every answer); `extractive` now says
    "verbatim quotes".

**The outage, because it generalises.** #9 shipped with a
`CREATE UNIQUE INDEX ON users (google_sub)` inside `_SCHEMA`, which runs *before*
the `ALTER`s that add the column. Fresh databases were fine — which is every
test — and every **existing** one raised `UndefinedColumn` out of
`AuthStore.__init__`, so the API never started. **A fresh database cannot test a
migration.** Three regression tests now start from the pre-Google table on
purpose (two SQLite, one Postgres).

---

## Google sign-in — configured and verified

- Flow: **ID token**, not authorization code. There is **no client secret**;
  `EURAG_GOOGLE_CLIENT_ID` is public by design and reaches the frontend at
  runtime via `/healthz`. Verification (`core/security/google_oauth.py`) is the
  whole security boundary — the `aud` check is the load-bearing one.
- Client id lives in `~/eurag/.env` on the VM:
  `EURAG_GOOGLE_CLIENT_ID=32881359423-…apps.googleusercontent.com`.
- **`https://eurag.duckdns.org` is an authorized JavaScript origin**;
  `http://localhost:3000` is **not**, so Google sign-in does not work in local
  dev unless you add it in the Google console. Everything else does.
- Verify an origin *without deploying* by serving a minimal GIS page **at** the
  origin under test (Playwright `context.route`) and watching whether
  `/gsi/button` returns `200` or `403`. Always probe a control origin you don't
  own — without it a `200` proves nothing. Method and results: DEVLOG.
- Google accounts and password accounts are **separate on purpose** and cannot
  be linked: identity keys on `google_sub` only, never username or email, or
  registering someone's username becomes account takeover.
- If sign-in ever fails at Google's own screen with `403: access_denied`, the
  OAuth consent screen has fallen back to *Testing* — only listed test users can
  sign in. That is a Google console setting, not an app bug.

---

## What shipped 2026-08-10 (privacy batch) — live

Deployed and verified on prod: `/privacy` and `/terms` return 200, the served
HTML contains **no third-party host at all**, and fonts serve from
`eurag.duckdns.org` (`200 font/woff2`). Two commits, `f9af78e..15cbfc0`.

Prompted by "why don't we ask for cookie consent?" — the audit answered it in
reverse. EURAG sets **no cookies**, runs no analytics and carries no tracker,
so there is nothing to consent to and **deliberately no banner** (do not "fix"
this later — `docs/UPDATE_LOG.md`). The actual gap was transparency.

- **`/privacy` and `/terms`** — what is stored, for how long, and that every
  question goes to **Anthropic in the US**, which the site had never said.
  Linked from the composer disclaimer, so anonymous visitors reach them.
- **`DELETE /account`** — self-service erasure (account, refresh tokens, saved
  chats, uploaded docs, stored key, lifetime quota row), confirmed by typing
  the username. Two-step control in the settings dialog. The audit trail
  survives **pseudonymised**, so deletion can't erase evidence of an attack.
- **Two live defects found on the way**: `erase_tenant("public")` was reachable
  from a user action (a user's tenant *is* their username and `public` is
  registerable — it would have erased all 47 documents), and a stateless
  15-minute JWT kept working after its account was deleted. Both fixed;
  `api/deps._still_exists` now costs one PK lookup per authed request.
- **SECURITY.md was documenting a control that doesn't exist** — "audit log
  append-only (SQLite triggers block UPDATE/DELETE)". There are no such
  triggers anywhere in the code. Corrected.

- **Fonts are self-hosted now** (`app/fonts.css` + `public/fonts/`, 26 files).
  A page view makes **no third-party request at all**; Turnstile fires at
  submit and Google Identity Services only after "Continue with Google".
- **`public` and `deleted_account` are reserved usernames** — a username is a
  tenant id and an audit actor, not a label.

- **Contact route** is `akashacharya.de@gmail.com` (`lib/legal.ts`), published
  on both pages. It is a legal document's contact address — if it ever stops
  being read, the notice stops being true.

**What this does NOT make the site.** "GDPR compliant" is a conclusion about an
organisation, not a property of a codebase, and it is **not claimed anywhere in
the copy** — don't add it. Still missing and none of it is code: no legal entity
named in the notice (Art. 13 wants the controller's identity), **no Art. 28 DPA
with Anthropic** or documented transfer mechanism, no lawyer has read the notice
against the real processing, and chat retention is "until you delete it" rather
than a policy. The safe public phrasing is the one already on the page: *no
cookies, no tracking, no analytics*, which is checkable.

## What shipped 2026-08-10 (business context) — local only, not deployed

Four optional, closed-vocabulary fields — country, company size band, sector,
AI role — offered on the intro screen and used to tailor answers. Replaces the
free-text `Industry · optional` box, which was per-query and ephemeral.

- **Enums, never free text.** The context sentence lands *outside* the
  `BEGIN SOURCES` fence, in the region the model is told to obey, and it is now
  persistent (it rides every future question, including saved chats). The user
  picks an option; the server writes the sentence (`core/profile.py`).
- **The AI field asks provider vs deployer**, not "do you use AI" — that is the
  distinction Reg. 2024/1689 turns on, and a boolean can't change an answer.
  Verified live: the same question returns Art. 26 deployer duties for one and
  Art. 53 GPAI provider duties for the other, with an honest gap statement about
  Arts. 8–17 not being in the corpus (DEVLOG).
- **Retrieval is untouched and it was proved, not assumed** — the harness
  returns identical numbers on the changed tree and on a stashed clean one.
- **Never a gate.** The composer stays live behind the intro block; Skip is
  remembered. The editor is its own modal because `SettingsModal` only renders
  when `account` is set — putting it there would have given anonymous visitors
  a dead button, for the third time in this codebase.
- `query outcome:` now carries `profile=…`, so **"which sectors ask questions"
  is countable for the first time** — see the open decision on industries below.
- `/privacy` updated: the profile is a new stored category, and the page now
  states that nothing infers a visitor's country from their IP.

**Before deploying:** this adds columns to `users`, so it exercises the exact
migration path that took the API down on 2026-08-10. They are in the guarded
`ALTER` list, and the pre-Google regression tests (SQLite + Postgres) now assert
the profile columns arrive too — but `logs api | head -40` after `up -d` is
still the check that matters.

## Open work, in priority order

1. **Billing alerts — still not set.** The one genuinely unbounded risk. A
   public URL spends the server's Anthropic key on anonymous full-quality,
   escalation-enabled answers. Set a spend limit on the Anthropic key and GCP
   alerts at $50/$150. `docs/DEPLOY.md` §5.6. The logged-in free tier is capped
   now (item 7 above); **anonymous is not** — that is what a spend limit covers.
2. **Cut the escalation rate — measure first.** The `query outcome:` telemetry
   shipped for exactly this and **has still never been read against real
   traffic**. Take the reading, then work the `primary_reason` histogram:

   - **No relevance floor** — the top lever. RRF ranks by relative position and
     the cross-encoder only reorders, so nothing checks whether the best chunk is
     *actually relevant*: `mode="no_sources"` never fires on a 47-doc corpus and
     every off-corpus question rides the full cascade. A score threshold
     returning `NO_SOURCES_MESSAGE` with **zero** LLM calls is the biggest cost
     lever on the anonymous tier. Needs threshold calibration.
   - **Split the escalation triggers.** `pipeline.query` gates on `insufficient`,
     conflating "corpus doesn't cover this" (wants deeper retrieval) with
     "citation validation failed" (wants a better prompt). `insufficient_reason`
     already carries the distinction; the gate doesn't read it yet.
   - **The two known phrase-precision misses escalate every time** — GDPR Art. 6
     lawful-bases and Late Payment statutory-interest retrieve the right doc but
     an adjacent slice, so each is a standing Opus tax. First pass caps 2
     chunks/doc; escalation's trick is `max_per_doc=6` — try 3 on the first pass.
     **Retrieval change → standing rule 1**: harness before/after, pinned with
     `EURAG_HYDE_MODEL=none`.
   - ~~`uncited` → a bug~~ **fixed 2026-08-09.** A high `uncited` count from here
     on is a genuine citation-formatting problem, not that bug.

3. **Move the seed lock out of the bind mount** — `data/raw/.seed.lock` →
   `/app/var/.seed.lock`. The `apivar` named volume is already owned by
   `eurag:eurag` and shared across replicas, so the flock still works and the
   manual `chown -R 10001:10001 data/raw` disappears from every future Linux
   deploy.
4. **Re-measure sizing on Linux.** `docs/DEPLOY.md` §4's memory numbers are
   macOS/arm64 and flagged as extrapolated. Capture
   `docker stats --no-stream eurag-api-1` during an escalated query (the Late
   Payment statutory-interest question reliably escalates) and replace them.
5. **90-day credit cliff** — trial started 2026-08-08, so **~2026-11-06**.
   Landing spot is Hetzner `CAX21` (~€7/mo, ARM — the original images were built
   arm64). `docs/DEPLOY.md` §6.

**Deferred:** password accounts have no email → **no password reset** (lose the
password, lose the saved chats). This is now *narrower* than it was — Google
sign-in gives new users a recovery-capable route — but it still bites anyone
who registered with a username and password. Also: registry-uploads-per-instance,
no streaming, no i18n, no monitoring.

## Decisions waiting on the user

- **Monetization** — BYOK-only or Stripe? Decides whether accounts need email.
  Now sharper: the free tier is 10 lifetime questions, so the funnel already
  ends at "add your own key". Stripe is the only alternative to that being the
  permanent answer.
- **Industries** (long-open) — which sectors matter for Tier-2 sector law.
  **Now actually measurable:** the sector is a fixed enum carried on the
  unconditional `query outcome:` line, so it aggregates. The old
  `query industry context:` line was free text and fired only when set, which is
  why it never answered anything. Once the batch is deployed:

  ```bash
  docker compose -f docker-compose.prod.yml logs api \
    | grep -o "sector=[a-z]*" | sort | uniq -c | sort -rn
  ```

  Still gated on there being real traffic — check the `query outcome:` count
  first; it remains the only reliable denominator.
- ~~**`EURAG_FREE_ANON_QUESTIONS`**~~ — decided 2026-08-09: **2**, live.
- ~~**Google OAuth client id**~~ — supplied 2026-08-10, live and verified.

---

## Traps that will cost you an hour (full list: CLAUDE.md → Gotchas)

- **A fresh database cannot test a migration.** `_SCHEMA` may contain only
  statements valid against the table's *old* shape; anything referencing a newly
  added column goes after the `ALTER`s in the guarded migration list. This took
  the live API down on 2026-08-10 and every test passed, because every test
  built its DB from scratch. On Postgres `executescript` is **one** statement, so
  one bad line discards the whole schema.
- **`0 documents indexed` means `/healthz` is failing**, not an empty corpus —
  see the box under *The live deployment*.
- **Pin `EURAG_HYDE_MODEL=none` before any retrieval A/B.** Expansion calls
  Haiku, so the harness is non-deterministic; the golden set has 3 compound
  cases, so one flip is 33pp. A "safer" rerank batch was nearly shipped on noise.
- **A per-query rate needs an unconditional log line.** `query outcome:` is the
  denominator. Keep grep-targeted strings **ASCII** — one failed count was
  grepping the em dash in `low-confidence answer — escalating to …`.
- **Don't re-tighten citation validation to always require a citation.** An
  honest refusal may cite nothing (marker present, ≤600 chars). The old
  unconditional rule rejected the model for obeying its own prompt and cost 4 LLM
  calls per refusal.
- **A "logged in but not allowed" response is 402/403, never 401.** The web
  client treats 401 as "refresh the session token" and loops. This has now cost
  thinking twice — `byok_key_rejected` and `free_limit_reached`.
- **Any per-user gate must cover BOTH logged-in ask paths** — `/query` with a
  bearer token *and* `POST /conversations/{id}/messages` (what saved chats use).
  Go through `api/deps.spend_free_question`; a gate on one door is not a gate.
- **Never gate a submit button on a third-party widget solving.** Ask for the
  token at submit time and show an error if you can't get one.
- **The bot gate is invisible locally.** No `EURAG_TURNSTILE_SECRET` means the
  server skips verification, so a client sending no token passes — which is
  exactly how `/login` shipped unable to register. Rehearse with Cloudflare's
  test keys (sitekey `1x00000000000000000000AA`, or `3x00000000000000000000FF`
  to force an interactive challenge; secret
  `1x0000000000000000000000000000000AA`) passed **on the command line, never in
  `.env`** — in `.env` they break 13 tests locally while CI stays green.
- **An error path that renders `{msg && …}` needs a guaranteed non-empty
  message.** A parser miss shows *no* error, which reads as a dead button.
- **`import core.config` first in any ad-hoc LLM script.** It loads `.env` at
  import time; without it you silently get `ExtractiveClient` and every LLM
  helper returns its fallback. **Check `type(llm).__name__` before believing a
  result.**
- **Chunker or registry-schema changes are a silent no-op** without
  `rm -rf var && python -m data.seed` (content-hash skip).
- **`api/main.py` reads settings twice on purpose** — don't collapse them.
- **Local dev shares `127.0.0.1`** between curl and the browser, so curl-testing
  the anon endpoint spends the browser's free questions.
- **A rewrite step inherits the promises of whatever it feeds** — that is how an
  English question came back in Spanish.
