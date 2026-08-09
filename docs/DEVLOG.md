# DEVLOG

Running log of build sessions. Newest first.

## 2026-08-09 (web UI) — the bot gate stops being the front door

Two complaints, one root cause: the Turnstile widget was rendered eagerly and
the UI was wired to *wait* on it.

**Measured before.** Driven with Chrome via `playwright-core` against a local
`uvicorn` + `next dev` pair, using Cloudflare's own test keys (sitekey
`1x00000000000000000000AA` always passes, `3x00000000000000000000FF` forces an
interactive challenge; secret `1x0000000000000000000000000000000AA`):

| | before | after |
|---|---|---|
| `.turnstile` height, anonymous chat, idle | **73px** (visible checkbox) | **0px** |
| `.turnstile` height while a challenge is up | 73px | 73px (unchanged — this is the case it *should* show) |
| Ask button on load | `disabled` until solved | enabled |
| Create-account button | `disabled` until solved | enabled |
| Widgets on screen with the register modal open | 2 visible boxes | 0 |
| `challenges.cloudflare.com` blocked → Ask | **`disabled` forever, no message** | enabled; error naming the blocked host |
| `/login` → create account | **403 `turnstile_failed`, unrecoverable** | works |

**What changed.** `Turnstile` renders `appearance: "interaction-only"` +
`execution: "execute"` and exposes `getToken()`; `send()` and the register
submit mint a fresh single-use token at submit time. `/login` became a redirect
to `/chat?auth=login` — it was a pre-bot-gate copy of the form that posted
`/auth/register` with no token and no widget, and nothing linked to it, so it
had been broken in prod since the gate shipped. Reasoning for both:
[UPDATE_LOG.md](UPDATE_LOG.md).

**Verified end to end**, each in a fresh browser context against the real
Cloudflare endpoint:

- anonymous question → invisible challenge → `200 POST /query`, answer with 2
  resolvable citations, `1 free question left`;
- `/login` → modal in sign-in mode → create account → `200` register + login +
  `/account`, sidebar shows the username; sign out → sign back in → works;
- quota wall (`EURAG_FREE_ANON_QUESTIONS=0`) → forced non-dismissable modal in
  register mode → account created and signed in;
- forced-interactive sitekey → container goes 0px → 73px, class flips to
  `turnstile active`, thread reads *"Waiting for the Cloudflare check below…"*;
- Cloudflare blocked → both entry points fail with the message instead of
  hanging.

`npx tsc --noEmit` clean, `next build` clean (`/login` 332 B, static), Python
suite unchanged at **237 passed / 6 skipped** (no backend change — the server's
fail-closed policy on a missing token is deliberately untouched).

## 2026-08-09 (telemetry) — making the escalation rate countable

Started from "how do we escalate fewer questions?" and got no further than the
first step: **the escalation rate was not measurable.** The eval harness is
retrieval-only (`doc_hit` / `doc_mrr` / `phrase_hit`) and never calls the
answerer, so it cannot see escalation at all. In prod the only trace was
`low-confidence answer — escalating to …`, a bare count with nothing to divide
by: every other per-query log line fires on a branch
(`query industry context:` only with an industry set, the contextualiser line
only on follow-ups), so **there was no denominator anywhere.**

Confirmed by trying it. A first pass at counting returned `0` and `0`, which
was uninterpretable — no traffic, no log history, and a broken pattern all look
identical without a denominator. One of the two patterns also contained the em
dash from the source string, which would have failed on encoding alone.

**Shipped:** one unconditional `query outcome:` line at the end of
`pipeline.query`, ASCII-only:

```
query outcome: mode=llm escalated=True primary_reason=uncited insufficient=False citations=4
```

Escalation rate is now `grep -c "escalated=True"` over `grep -c "query
outcome:"`, and `primary_reason` says which of two unrelated causes paid for it.

**`AnswerResult.insufficient_reason`** carries that cause — `marker` (the model
said the sources don't cover the question), `uncited` (two generations failed
citation validation), `no_sources` (retrieval returned nothing). The escalation
gate still reads `insufficient` alone; this is telemetry only, and it is
deliberately **not** in `to_dict()` — the API response shape is a contract.

**The measurement this exists to take.** A probe confirmed that an honest
zero-citation refusal — exactly what `SYSTEM_PROMPT` asks for when the sources
don't cover the question — is rejected by `validate_answer` (which requires
≥1 citation unconditionally), retried, rejected again, downgraded to extractive
with `insufficient=True`, and *then* escalated, where the strong model repeats
the same loop:

```
primary calls: 2   escalation calls: 2   mode: extractive   citations: 3
```

Four LLM calls, 2 Sonnet + 2 Opus, against a cost model that assumes one of
each — and the user gets three verbatim quotes from the chunks the model just
refused to use, instead of the honest "not in the corpus" it wrote. Every
off-corpus question takes this path, because nothing thresholds relevance: RRF
ranks by relative position and the cross-encoder only reorders, so
`mode="no_sources"` fires only on a literally empty result, which a 47-document
corpus never returns.

### Then the fix, same session

The instrument was built to shipped separately so the "before" rate could be
captured first. That ordering was overtaken — the fix was requested immediately,
and it cost nothing, because the log window held **no traffic to measure**
(the first count came back `0` against a `0` denominator). Nothing was lost;
the telemetry's value is now forward-looking.

`answer_question` accepts a zero-citation answer **when the insufficiency
marker is present and the text is short** (`MAX_UNCITED_REFUSAL_CHARS = 600`).

Two guards keep the citation discipline intact, and both are tested:

- **`not used`** — an out-of-range marker leaves `used` non-empty, so a refusal
  citing a fabricated `[9]` is still rejected. The only failure this rescues is
  literally "no citations."
- **the length cap** — a sentence or two is a refusal; a long uncited body is a
  substantive answer with the marker tacked on, and shipping *that* would be
  the uncited claim the whole product forbids. Over the cap, the old extractive
  downgrade still applies.

An answer with no marker is untouched — the model believing it answered, with
no citation, remains a validation failure.

**Effect on the path measured above** — 4 LLM calls become 2:

```
before:  primary 2 + escalation 2  -> mode=extractive, 3 misleading quotes
after:   primary 1 + escalation 1  -> mode=llm, the model's own refusal
```

**The escalation still fires, and should.** `primary_reason` moves from
`uncited` to `marker`: a model reporting a corpus gap is exactly the case
deeper retrieval exists to rescue. This change halves the price of finding out
and fixes what ships when the rescue fails; the escalation *count* is a
different lever (the missing relevance floor — HANDOFF open item 3).

Tests: 237 pass / 6 skip (+16). No harness run — retrieval is untouched.

## 2026-08-09 — live on a public URL, the first production OOM, and follow-ups

Three things in one day: the deploy, an OOM the deploy exposed, and a retrieval
bug the browser pass exposed.

### Follow-up questions retrieved the wrong act

The browser pass (the last unverified item from the live-safety batch — the
Turnstile widget had never actually been rendered and clicked) worked, and
immediately surfaced a real bug. After a good GDPR answer about data protection
officers, the follow-up **"what if I have 29 people?"** was answered against the
**Pay Transparency Directive**, and the model itself said the question was *"too
vague on its own"*.

Root cause: `pipeline.query()` took no history at all. Conversations were stored
in `core/conversations.py` for display only — prior turns never reached
retrieval or generation. Stripped of its conversation, the follow-up's only
lexical signal is a headcount, which matches Pay Transparency's "fewer than 100
workers"; BM25 and the vector leg agreed on the wrong act and the reranker
faithfully ranked its passages. HyDE made it *worse*, expanding the fragment in
the wrong direction.

Fix: **`QueryContextualizer`** (`core/retrieval/expansion.py`) rewrites a
follow-up into a standalone question before retrieval, from prior turns, via
Haiku. One rewrite fixes both halves — retrieval gets a real topic, and because
the rewritten question is what the answerer receives, it never sees a fragment,
so `answerer`'s cite-or-fail discipline is untouched. It runs **before** HyDE;
the opposite order expands a fragment. Bad rewrites degrade to the raw query:
empty, multi-line, or over-long replies are rejected, because a wrong rewrite
silently retrieves the wrong act and is worse than no rewrite.

**Where history comes from differs by tier, deliberately.** Anonymous users have
no saved conversation, and anonymous is the default demo path — so the client
sends its turns, capped like `/ingest` fields (≤10 turns, ≤2000 chars each,
last 3 used, answers truncated to 400 chars) because that is untrusted text
entering a prompt. `/conversations/{id}/messages` already loads the stored chat
before querying, so it reads history server-side and trusts nothing from the
client.

**Harness (standing rule 1).** Three follow-up cases added to the golden set,
including the exact live sequence. They are not self-contained by design, and
their failure is not random — each one's only standalone signal points at a
different act.

| | before | after |
|---|---|---|
| doc_hit | 94% | **100%** |
| doc_mrr | 0.94 | **1.00** |
| phrase_hit | 87% | **94%** |
| compound_hit | 100% | 100% |
| follow-up cases | **2 of 3 MISS** | **3 of 3 rank 1, phrase ✓** |

Single-turn cases are unmoved — 30/32 phrase hits still leaves the known Late
Payment miss exactly where it was. The third follow-up ("what about the
withdrawal period?") passed *before* the fix too: "withdrawal period" is
lexically distinctive enough to stand alone. It was kept precisely because it
shows not every follow-up is broken, so the metric can't be gamed by assuming
they all are.

Cost: one Haiku call, and only on questions that actually carry history. First
questions are untouched, and local single-user mode never sends history, so it
is inert there.

Follow-up cases **skip** in `tests/evaluation/test_golden_retrieval.py` — that
suite is offline by design and there is no LLM to rewrite with, so asserting
them would test nothing. Same treatment as a `core=False` case whose document
isn't in the corpus: skip honestly rather than fail or silently pass.

Tests 212 → **220 passed, 6 skipped**.

### …and the fix had a bug of its own: it translated the question

Deployed, the follow-up resolved correctly to GDPR Article 37 — and answered
**in Spanish** to an English question.

`answerer` already promises *"write the answer in the same language the
question is written in — never switch languages on your own"*, and it obeyed:
it matched the language of the question **it was handed**, which was the
contextualiser's rewrite. `_CONTEXTUALIZE_SYSTEM` said nothing about language.
**Inserting a rewrite in front of a component moves that component's
input-shaped promises onto the rewrite** — the guarantee was still enforced, on
text the user never wrote.

Two things went wrong while fixing it, both worth recording:

1. **Emphasis backfired.** The first attempt led with the language rule, hard
   ("ALWAYS … SAME LANGUAGE … never translate"). The model read it as *don't
   change the question* and stopped rewriting altogether. The working version
   leads with the rewrite task, shows a concrete before/after example, and
   demotes language to a subordinate clause: *"resolving references is not
   translating"*.
2. **The verification script was measuring nothing.** It imported
   `core.generation.llm_client` directly, so `core/config.py`'s import-time
   `_load_dotenv()` never ran, `ANTHROPIC_API_KEY` was unset, `get_llm_client`
   returned `ExtractiveClient`, and `standalone()`'s deliberately-silent
   fallback returned the raw query every time. That looked exactly like a total
   regression. **Check `type(llm).__name__` before believing an LLM result.**

Verified against real Haiku once the client was actually an `AnthropicClient`:
an English follow-up rewrites to English (4/4), a French one to French (3/3),
an already-standalone question passes through untouched. Multi-constraint
threads survive ("German clinic … patient health records … 29 employees" all
carried, 3/3), and a mid-thread correction overrides correctly ("actually we
are a public hospital" → the rewrite says public hospital, not clinic).

Harness unmoved by the prompt change: `doc_hit=100%`, `doc_mrr=1.00`,
`phrase_hit=94%`, all three follow-ups rank 1. Tests **221 passed, 6 skipped**.

### Live deploy

EURAG went live at `https://eurag.duckdns.org` on a GCP `e2-medium` (2 vCPU /
4 GB, europe-west3) on trial credit. `/healthz` reported the whole contract
holding on the first boot: `documents=47`, `embedder=fastembed:…MiniLM-L12-v2`
(strict boot held — no silent hash fallback), `auth_enabled=true`,
`encryption=true`, and the real Turnstile sitekey served. Caddy took a
Let's Encrypt cert on the first attempt, which finally exercised the Phase 7
`EURAG_DOMAIN` fix in anger. The first-ever CI run went green on all three
jobs, including the two that had never executed anywhere: pytest on py3.11
(local dev is 3.13) and the postgres-parity job.

**A third prod-only bug surfaced at bring-up.** `data/raw/` is gitignored, so
it does not exist in a fresh clone, and Docker created the bind-mount source
as `root:root`. The image runs as `USER eurag` (uid 10001), which cannot write
`data/raw/.seed.lock` — the seeder died in 5s with `PermissionError`, and
`service_completed_successfully` correctly refused to start the API behind it.
Unblocked with `chown -R 10001:10001 data/raw`. **This was invisible in the
Phase 7 local rehearsal**: Docker Desktop on macOS remaps bind-mount ownership,
so the failure mode only exists on Linux. Worth remembering that the macOS
rehearsal validates behaviour, not permissions.

**Then the escalation path OOM-killed the API.** A follow-up question escalated
(`low-confidence answer — escalating to claude-opus-4-8 over wider retrieval`),
the container died mid-request with no traceback, and the next two questions
returned `HTTP 502` until `restart: unless-stopped` brought it back.
`dmesg` showed one `killed process`, and the api container showed exactly one
restart and two cold starts.

Root cause: `hybrid_retriever` builds `pool = max(k*5, 30)` when reranking —
**30 candidates at `top_k=6`, but 60 on the escalation path at
`EURAG_ESCALATION_TOP_K=12`** — and `CrossEncoderReranker.rank` called
fastembed's `rerank()` without `batch_size`, taking its default of **64**.
That default sits above every pool this retriever can build, so the whole pool
always went through in a *single* forward pass, and the escalation path
allocated double the activations of a normal query.

Measured (peak RSS per process, one process per batch size, 60 real corpus
chunks — the escalation pool):

| batch | peak RSS | rerank allocation | time |
|---|---|---|---|
| **64 (old default)** | 1948 MB | **1607 MB** | 2.27s |
| 32 | 1424 MB | 1080 MB | 1.85s |
| 16 | 949 MB | 605 MB | 2.58s |
| **8 (new default)** | **627 MB** | **284 MB** | 2.71s |

A single rerank step was allocating **1.6 GB**, on top of an 835 MB resident
baseline, on a 3.8 GiB host. Fix: `EURAG_RERANK_BATCH` (default **8**),
threaded `config → pipeline → get_reranker → CrossEncoderReranker`. That is a
**5.7× cut in transient allocation for +0.44s** — noise beside a multi-second
Claude call — and it keeps the box at 4 GB instead of forcing a resize.

**Harness (standing rule 1): no movement.** Cross-encoder scores are per-pair
independent, so batching bounds memory without touching ranking. With
expansion disabled for determinism, every batch size from 4 to 64 is identical:

```
EURAG_HYDE_MODEL=none, batch ∈ {4, 8, 16, 32, 64}
k=6  cases=29  doc_hit=100%  doc_mrr=1.00  phrase_hit=93%  compound_hit=67%
```

**Methodological finding worth keeping: the harness is not deterministic with
HyDE on.** The first A/B appeared to show `compound_hit` 100%→67% at batch=8 —
but a repeat run of the *same* config gave 100%, and batch=12 then gave 67%.
Expansion calls Haiku, so each run gets a different hypothetical document,
a different candidate pool, and one knife-edge compound case flips with it.
**A single-case metric delta is not attributable to a code change unless
`EURAG_HYDE_MODEL=none` pins the expansion.** The golden set has only 3
compound cases, so one flip is 33 points — that metric is low-resolution by
construction. (Incidentally this also re-confirms HyDE's value: compound_hit is
consistently 67% with expansion off and reaches 100% with it on.)

Also corrected: `docs/DEPLOY.md` §4 measured idle and peak-while-answering but
never measured the **escalation** path, so it published a sizing floor derived
from the common case and missed the true maximum by ~1.5 GB.

Tests 209 → **212** (batch size is forwarded, `get_reranker` forwards it, and
ordering is batch-invariant) — a silent regression here would cost 5.7× peak
memory with no other symptom.

## 2026-07-25 → 08-08 — "make it live safely": blockers, hardening, CI, prod bring-up

v1.0.0 was feature-complete but not safely deployable. A sanity pass found: a
clean build seeds **4 documents instead of 47** (`data/raw/` is gitignored and
the loaders silently skip); the anonymous tier exposes Opus-capable calls with
**no bot protection**; missing shared secrets only *logged* instead of refusing
to boot; the prod **web image could not build** (`Dockerfile` copied a
nonexistent `public/`); both API replicas re-seeded on every fresh container;
LLM failures surfaced as raw 500s and **burned an anon free question**; and
there was no CI. Eight phases, one commit. Design decisions (D1–D17) are in
`context_files/PLAN_LIVE_SAFETY.md`. **No phase touched retrieval**, so the
before/after-harness standing rule did not apply.

- **Corpus reproducibility (D1–D5).** `data.seed` grew a CLI: `--scrape` fills
  missing caches, `--expect-docs N` exits 1 below N so a short corpus fails a
  deploy loudly, and an `flock` on `data/raw/.seed.lock` serializes replicas. A
  one-shot **`seeder`** service in the prod compose runs to completion before any
  api replica starts (`service_completed_successfully`), with shared `apivar` +
  `modelcache` volumes so replicas never seed, scrape, or re-download the ~200MB
  of ONNX models. `EURAG_STRICT_BOOT` makes degradation fatal: the embedder
  **raises** instead of silently falling back to the hash embedder (same
  dimension, undetectable, would poison shared Qdrant), and a failed seed kills
  the container. The funding snapshot date now comes from the cache file's mtime
  rather than `date.today()`, so its content hash is stable — no daily re-embed.
  Registry connections got `WAL` + `busy_timeout`.
- **Bot gate at the anonymous boundary (D6–D10).** `core/security/turnstile.py`
  — `verify()` rejects a missing token with no network call, fails **closed** on
  an explicit `success:false`, and fails **open** on a Cloudflare outage (the
  per-IP quota still holds; an outage must not kill the funnel). Gated *before*
  `anon_quota.consume` in `/query` and on `/auth/register` (bot signups would
  otherwise bypass the anon quota and get server-key answers). The **sitekey is
  served from `/healthz`**, so rotating keys is an env change with no rebuild.
  Built and verified against Cloudflare's universal test keys.
- **Startup secret guard (D17).** `validate_startup()` runs at import time:
  raises when `auth_enabled` + a Postgres URL are set without
  `EURAG_JWT_SECRET` (per-instance auto-secrets silently break multi-instance
  login), warns when `EURAG_ENCRYPTION_KEY` is missing. Local zero-config
  untouched.
- **LLM failure handling + quota fairness (D11, D12).** `LLMUnavailableError`
  maps SDK exceptions to a `kind`; an app-level handler turns a rejected BYOK key
  into **400 `byok_key_rejected`** (not 401 — the web client treats 401 as
  "refresh the session") and everything else into **503 `llm_unavailable` +
  `Retry-After: 10`**. Anonymous questions are consume-then-**refund** on
  failure (consume-on-success would reopen a parallel-request overrun).
  Escalation is best-effort — a failed retry keeps the primary answer — and a
  latent crash was fixed where the log read `self.escalation_llm.name`, which is
  `None` on BYOK-only requests.
- **Hardening (D14, D15).** `/ingest` field caps (text 500k, url 2000, type 40,
  lang 16); `anon_quota` sweeps rows older than 2 days inside the existing
  consume transaction, on the insert branch only, so steady-state consumes stay
  a single UPDATE; `frontend/web/public/.gitkeep` unblocks the web image.
  **Scope note:** D15 named only the rate limiter, but `api/deps.client_ip` —
  the *anon quota* key — was trusting `X-Forwarded-For` unconditionally, so a
  forged header bought unlimited free full-quality questions on any directly
  reachable deploy. Both now share one helper, `deps.peer_ip(request,
  trust_proxy)`, behind `EURAG_TRUST_PROXY` (default off). That flipped
  `test_anonymous_quota_is_per_ip_not_shared`, which had asserted the unsafe
  behaviour; it was replaced by trusted/untrusted matrices on both surfaces.
- **CI (D16).** `.github/workflows/ci.yml` — three parallel jobs on push/PR:
  **python** (3.11, matching the API image), **web** (node 22, `npm ci && npm run
  build`), **postgres** (`postgres:16` service for the two dialect-parity tests).
  `tests/test_postgres.py` *skips itself* when the URL is unusable, so a broken
  service would pass green — an explicit `psycopg.connect` step runs first and
  fails the job instead.
- **Prod bring-up — the integration test.** Both images build; the full stack ran
  end to end. Seeder: **47 documents, exit 0, ~3m20s** cold (4202 chunks, populated
  `data/raw`); replicas healthy ~12s later; a re-run is **~4s** with all 47
  hash-skipped and zero re-embeds. `/healthz` reported 47 docs and
  `embedder=fastembed` (strict boot held). Smoke: no-token → 403 with the quota
  **untouched**; wall → 401; register 403/200; free tier no escalation; real BYOK
  → cascade fired; bad BYOK → **400, not 500**; key at rest is `enc1:` ciphertext;
  saved chats readable from every replica; limiter → 10 then **429 + Retry-After**
  with buckets in Redis. `docker kill` on one replica → 10/10 healthz plus a real
  cited answer. **A forged `X-Forwarded-For` did not mint a fresh quota key** —
  Caddy replaces the header — which upgrades D15's central assumption from
  *assumed* to *verified*.
- **Two real bugs found by the bring-up.** (1) The `caddy` service had **no
  `environment:` block**, so `EURAG_DOMAIN` never reached the container and the
  site address always fell back to `:80` — **auto-HTTPS could never have engaged
  in production.** Fixed with `EURAG_DOMAIN: ${EURAG_DOMAIN:-:80}`; the `:80`
  default *must* live at the compose layer, because Caddy applies its own default
  only when the variable is unset and an empty string collapses the site address
  (`caddy adapt` then dies with `unrecognized global option: encode`). (2)
  Putting the prod secrets in `.env` **failed 13 tests** — `pydantic-settings`
  reads `.env`, so `EURAG_TURNSTILE_SECRET` switched the bot gate on inside the
  suite. Not a code regression, but a local-only trap invisible to CI; rehearsal
  secrets now live in gitignored `.env.prod.local` passed via `--env-file`.
- **Sizing, measured** (`docker stats`): an api replica is **835MB idle but
  1.83GB while answering** — it more than doubles. Whole stack **2.2GB idle /
  3.2GB peak** at two replicas, ~1.4GB / **~2.4GB** at one. Consequence: every
  1GB "always free" VM (AWS `t3.micro`, Azure `B1S`, GCP `e2-micro`) is
  unusable; the floor is a 4GB box.
- **Docs.** `docs/DEPLOY.md` rewritten (73 → ~290 lines) with boot order, the
  shared-state table, strict boot, the trust-proxy model, measured sizing, a
  GCP free-tier runbook, and backups. Deploy target settled as GCP `e2-medium`
  on trial credit — Oracle Always Free would have been free-forever and fits,
  but account signup was rejected.
- Verified at commit: **209 passed, 3 skipped**; `npm run build` clean.

## 2026-07-08 — access tiers: anonymous free questions, login wall, BYOK
Removed the forced login and added the cost-control model the user specified.
- **Anonymous tier**: `/query` now works without a token — 3 (configurable)
  full-quality questions, **counted server-side per IP/day** (`core/quota.py`
  on the shared DB, so clearing browser state doesn't reset it). Spent → 401
  `anonymous_limit_reached`. The frontend popup only reflects the server count.
- **Login wall**: frontend is anonymous-first (`app/chat/page.tsx` reworked to
  handle anon + authed modes). After the free questions a forced login modal
  appears; register/login → authed mode with saved chats. "Continue with
  Google" button is present but disabled (needs a Google OAuth client the owner
  must create — clean seam left).
- **Model tiering** (`pipeline.query` gained answer_model/escalation_model/
  api_key overrides, per-request client cache): anonymous + BYOK get the full
  Sonnet→Opus cascade; logged-in free tier gets Haiku, no escalation. Local
  (auth-off) mode is untiered (full cascade), so nothing changed for local dev.
- **BYOK** (`api/routes/account.py`, `AuthStore.set/get/clear_byok`): a user
  stores their own Anthropic key, AES-256-GCM encrypted (requires
  EURAG_ENCRYPTION_KEY); their queries run on their key + full cascade. Verified
  the raw key never lands in the DB (only `enc1:` ciphertext) and is never
  returned; `account.byok_set` audited.
- Verified end-to-end in a browser: 2 free anon answers with citations and a
  live "N free questions left" counter → 3rd triggers the wall → register →
  free-tier banner → add key → banner clears (premium). 163 tests + 6 new tier
  tests (anon gate, per-IP isolation, wall, free/byok tiers, key never leaked).
- **Note for deploy**: no global $ ceiling by product choice (a genuinely hard
  question should be allowed to escalate); the residual IP-rotation risk on the
  3 free full-quality questions is mitigated by Turnstile — seam left, needs the
  owner's Cloudflare site key. SECURITY.md documents the model.

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
