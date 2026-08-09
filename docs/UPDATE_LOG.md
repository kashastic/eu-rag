# UPDATE LOG — decisions and gotchas

Dated, discrete entries. Newest first. Each one is either a **[DECISION]** (a
choice made, with the reason, so it isn't silently reversed later) or a
**[GOTCHA]** (something that cost time once and shouldn't cost it twice).

**Where things live**, so these four don't drift into each other:

| File | Holds | Shape |
|---|---|---|
| **this file** | one entry per decision or gotcha, dated | scannable list |
| [`DEVLOG.md`](DEVLOG.md) | what was built each session, with before/after numbers | narrative |
| [`../CLAUDE.md`](../CLAUDE.md) | the short list a new session must read first | standing rules |
| [`../context_files/PLAN_LIVE_SAFETY.md`](../context_files/PLAN_LIVE_SAFETY.md) | design decisions D1–D17 of the live-safety batch | approved plan |

Rules of thumb: if it's **load-bearing for every future session**, it also
belongs in CLAUDE.md's Gotchas (short form there, full reasoning here). If it
comes with **measurements**, the numbers go in DEVLOG and this file links to
them. Don't paste the same paragraph into three files — link.

---

## 2026-08-09 (web UI)

### [GOTCHA] FastAPI's `detail` has three shapes, and the one we didn't parse made every 422 silent

**Symptom.** Reported from the live site: *"nothing happens when I click Create
account or Sign in."* The button un-greyed and no error appeared. The password
was 9 characters against a 10-character minimum.

**Why.** `lib/api.ts` parsed the error body as either a plain string or one of
our structured `{code, message}` objects:

```js
const message = d && typeof d === "object" ? d.message : d || `HTTP ${status}`;
```

FastAPI returns **request-validation** failures as an *array* of pydantic
errors. `typeof [] === "object"`, so the array took the object branch and
`d.message` was `undefined` → `new ApiError(422, undefined)` → `err.message` is
`""` → `{error && <p className="err">{error}</p>}` rendered **nothing**, because
the empty string is falsy. Not just the password: every field-level 422 in the
app was invisible the same way (username length/charset, a 2-character
question).

**Fix.** `errorFrom()` handles all three dialects and is contractually unable to
return an empty message. Both call sites also fall back to a non-empty string,
and the login form now checks the API's own bounds client-side so a short
password is answered instantly instead of costing a round trip.

**The general rule.** An error path that renders `{msg && …}` needs a
**guaranteed non-empty** message — otherwise a parser miss doesn't show a wrong
error, it shows *no* error, which the user reads as a dead button. Test the
failure shapes, not just the happy path: this was reachable from the very first
form on the site, and the whole flow had been verified end to end with *valid*
input.

### [DECISION] Turnstile is invisible and runs at submit time, never on page load

**Choice.** `components/Turnstile.tsx` renders with `appearance:
"interaction-only"` + `execution: "execute"` and exposes one imperative method,
`getToken()`. Callers mint a fresh single-use token **when the form is
submitted**. No button is ever disabled waiting for a challenge.

**Why.** The old widget rendered `appearance: "always"` on mount and the parent
gated Ask / Create-account on `!tsToken`. Three things fell out of that, and the
third is the serious one:

1. A **72px Cloudflare checkbox sat permanently above the composer** on every
   anonymous page load — the first thing a visitor saw on a page whose whole
   pitch is "ask a question."
2. It solved on load, so by the time a visitor typed a question the token could
   already be minutes into its 300s life. Execute-on-demand is always fresh.
3. **A challenge that never completes left a dead UI with no error text.**
   Measured with `context.route(...abort())` on `challenges.cloudflare.com`:
   Ask *and* Create-account were `disabled` forever and nothing on screen said
   why. Any ad blocker, privacy extension, corporate proxy, or sitekey/domain
   mismatch reproduces it — and a sitekey mismatch is exactly the failure a
   fresh deploy is most likely to hit.

**The general rule.** *Never gate a submit button on a third-party widget's
success.* Ask for the token at submit time, and treat "couldn't get one" as an
error you show, not a state you sit in. `TurnstileUnavailableError` carries a
visitor-facing message naming the likely cause (blocked
`challenges.cloudflare.com`); the server's fail-closed policy on a missing token
is unchanged — see [SECURITY.md](SECURITY.md).

**Two details worth keeping.** The container is a **0px box** until Cloudflare
paints a challenge into it, so spacing is driven by an `.active` class set from
`before-interactive-callback` / `after-interactive-callback` — the widget lives
in a shadow root, so there is no child selector (`:has(iframe)` matches
nothing). And `CHALLENGE_TIMEOUT_MS` is **120s**, not 30s: an interactive
challenge is waiting on a person noticing a widget that just appeared. While it
is up, the thread says "Waiting for the Cloudflare check below" instead of
"Consulting the corpus", which would be a lie.

### [GOTCHA] A second copy of a form is a second thing to keep correct — `/login` had rotted

**Symptom.** Creating an account at `/login` failed with *"Verification failed —
please retry the challenge"* on any deploy with the bot gate on, with no way to
recover from the page.

**Why.** `app/login/page.tsx` was a standalone copy of the sign-in form written
before the bot gate existed. It posted `/auth/register` with **no Turnstile
token and no widget to produce one**, so `api/routes/auth.register` rejected it
every single time. The modal on `/chat` had been updated; this copy had not, and
nothing linked to it (`/` redirects to `/chat`), so it was never exercised.
Locally it "worked" because no Turnstile secret is configured — the gate is
skipped, which is exactly why this only ever failed in production.

**Fix.** `/login` is now a redirect to `/chat?auth=login`; the modal is the one
sign-in UI. The query param is read from `window.location` rather than
`useSearchParams` so the client page still prerenders without a Suspense
boundary.

**The general rule.** When a gate is added to one entry point, grep for the
other callers of the endpoint it protects — a duplicate form that isn't linked
is a duplicate form nobody will notice is broken.

## 2026-08-09

### [GOTCHA] A per-query metric needs an unconditional log line, or it has no denominator

**Symptom.** Counting production escalations returned `0` and `0`. Nothing in
that result distinguishes "it never happened" from "there was no traffic" from
"the log history was wiped by a redeploy" from "the grep pattern was wrong."

**Why.** Every per-query log line in the pipeline fired on a *branch* —
`query industry context:` only when an industry is set, the contextualiser line
only on follow-ups, the escalation line only on escalation. So the numerator
had a log and the denominator had none. A second trap sat underneath: one of
the patterns contained the **em dash** from `"low-confidence answer — escalating
to %s"`, which can fail on encoding before it ever reaches a real question about
traffic.

**Fix.** `pipeline.query` now emits exactly one `query outcome:` line per query,
unconditionally and in ASCII, with the fields needed to divide:
`mode`, `escalated`, `primary_reason`, `insufficient`, `citations`.

**The general rule.** Before instrumenting a rate, ask what emits the
denominator. If the answer is "the same branch as the numerator," the number
will be uninterpretable no matter how carefully it is counted. And keep log
strings intended for grepping ASCII-only.

### [DECISION] An honest refusal may cite nothing — but only if it is short

**Choice.** `answer_question` accepts a zero-citation answer when the
insufficiency marker is present and the text is ≤ `MAX_UNCITED_REFUSAL_CHARS`
(600). Longer uncited bodies, fabricated markers, and uncited answers without
the marker all keep the old extractive downgrade.

**Why.** `SYSTEM_PROMPT` tells the model to cite nothing when the sources don't
cover the question; `validate_answer` required ≥1 citation unconditionally. The
model was being **rejected for obeying** — refusal, retry, refusal, downgrade to
verbatim quotes from the very chunks it had just refused to use, then escalation
repeating the whole loop on the expensive model. Four LLM calls, and a
user-facing answer worse than the one the model wrote. Numbers in
[`DEVLOG.md`](DEVLOG.md).

**Why a length cap rather than trusting the marker.** The marker alone is not
enough: a model could emit a full substantive answer with `INSUFFICIENT_SOURCES`
tacked on, and accepting that would ship an uncited legal claim — precisely what
citation enforcement exists to prevent. A refusal is a sentence or two. Length
is a crude signal, but it fails in the safe direction (a long refusal merely
gets the old behaviour), which no content heuristic would.

**The `not used` condition is load-bearing**, not redundant with "no citations":
an out-of-range marker leaves `used` non-empty, so a refusal citing a
hallucinated `[9]` is still rejected. The only validation failure this rescues
is the literal absence of citations.

**What it does not do.** The escalation still fires — `primary_reason` just
moves from `uncited` to `marker`. A model reporting a corpus gap is the case
deeper retrieval exists to rescue. This halves the cost of that path; reducing
the escalation *count* needs the relevance floor (HANDOFF open item 3).

### [DECISION] Instrument the escalation rate before fixing what inflates it

**Choice.** The `query outcome:` telemetry was built to ship ahead of the fix
above, so the pre-fix rate could be measured. **In the event both shipped in the
same session** — the fix was requested immediately, and the ordering cost
nothing because the log window contained no traffic to measure (the first count
came back `0` over a `0` denominator).

**Why it is still recorded.** The reasoning holds for the next one: a fix and
its own measurement are entangled, and shipping them together forfeits the
evidence of whether the fix was worth making. It was safe to collapse here only
because the "before" number was known to be empty. Check that before collapsing
them again.

**Reversible.** The telemetry is one log line and one unexposed dataclass
field; nothing downstream reads them.

### [GOTCHA] A rewrite step upstream of a guarantee must carry that guarantee

**What.** After follow-up contextualisation shipped, an **English** question
came back **answered in Spanish** in production.

**Why.** `answerer`'s prompt already says *"Write the answer in the same
language the question is written in — never switch languages on your own."*
It obeyed perfectly — it answered in the language of the question **it was
handed**, and that question was the contextualiser's rewrite, which had
silently translated. The guarantee lived at the answerer, but the answerer no
longer sees the user's words.

**The general rule.** Inserting a rewrite step in front of a component that
promises something about its input **moves the responsibility for that promise
to the rewrite**. Before adding another pre-retrieval or pre-generation
rewrite, re-read `answerer`'s prompt and carry every input-shaped promise into
the new step.

**Do this.** `_CONTEXTUALIZE_SYSTEM` now pins the output language, and
`tests/unit/test_expansion.py` asserts the constraint is still in the prompt —
behaviour depends on a model, but deleting the line would reintroduce the bug
invisibly.

**Also learned here:** phrasing matters more than emphasis. The first attempt
put the language rule first and stated it forcefully; the model read it as
"don't change the question" and stopped rewriting entirely. The working version leads
with the rewrite task, gives a concrete before/after example, and states the
language rule as a subordinate constraint ("resolving references is not
translating").

### [GOTCHA] `.env` only loads if `core.config` is imported

**What.** A scratch script that imported `core.generation.llm_client` directly
got `ExtractiveClient` and every LLM call silently fell back to the raw query —
making a prompt change look like a total regression when nothing was wrong.

**Why.** `core/config.py` calls `_load_dotenv()` **at import time**. Import
`core.config` (or anything that pulls it in, like `core.pipeline`) and the key
is present; skip it and `ANTHROPIC_API_KEY` is simply unset.

**Do this.** Put `import core.config` at the top of any ad-hoc script that
touches an LLM, and **check `type(llm).__name__` before trusting a result** —
`ExtractiveClient` means you measured nothing. The `standalone()` fallback is
deliberately silent on error, which makes this failure look like a bad rewrite
rather than a missing key.

### [GOTCHA] A follow-up question has no topic of its own

**What.** "what if I have 29 people?" retrieved the **Pay Transparency
Directive** after a GDPR conversation about data protection officers, and the
answer opened with *"too vague on its own"*.

**Why.** `pipeline.query()` took no history. Conversations were persisted for
display only, so prior turns never reached retrieval *or* generation. Stripped
of context the fragment's only signal is a headcount — which matches Pay
Transparency's "fewer than 100 workers" — so BM25 and the vector leg agreed on
the wrong act and the reranker faithfully ranked its passages.

**Do this.** `QueryContextualizer` rewrites the follow-up into a standalone
question before retrieval. **Order matters: contextualisation runs before
HyDE** — expanding a fragment amplifies the wrong topic rather than fixing it.

**Watch for.** Not every follow-up is broken. "what about the withdrawal
period?" resolves fine unaided, because it is lexically distinctive. That case
is kept in the golden set so the metric can't be gamed by assuming all
follow-ups fail.

**Detail:** [DEVLOG 2026-08-09](DEVLOG.md).

### [DECISION] A rewritten question, not history passed to the answerer

**Choice.** Contextualise the *query* and send the rewritten form downstream,
rather than plumbing conversation history into the answerer's prompt.

**Why.** One change fixes both halves of the bug: retrieval gets a real topic,
and the answerer receives a self-contained question so it never sees a
fragment. `answerer`'s cite-or-fail discipline and its prompt are untouched,
which keeps the blast radius off the part of the system that guarantees every
claim is cited.

**Trade-off accepted.** Answers won't refer back conversationally ("as I
mentioned above"). For a citation-first compliance tool that is arguably
correct — each answer stands alone with its own sources.

**Safety.** A bad rewrite is worse than none, because it silently retrieves the
wrong act. Empty, multi-line, or over-long replies are rejected and the raw
query is used instead.

### [DECISION] Anonymous sends history; logged-in reads it server-side

**Choice.** `/query` accepts history in the request body.
`/conversations/{id}/messages` ignores the client and reads the stored chat it
already loaded.

**Why.** Anonymous users have no saved conversation, and anonymous is the
default demo path — the bug was found there. Logged-in requests already have a
server-owned transcript, so trusting the client there would add untrusted input
for no benefit.

**Consequence.** The `/query` caps are load-bearing, not decorative: ≤10 turns,
≤2000 chars per field, only the last 3 used, answers truncated to 400 chars.
This is client-supplied text entering an LLM prompt, so it is bounded the same
way `/ingest` fields are.

### [GOTCHA] The retrieval harness is not deterministic with HyDE on

**What.** `core.evaluation.harness` calls HyDE expansion, which calls Haiku.
Every run therefore gets a different hypothetical document, a different vector
query, and a different candidate pool. Metrics move between runs of *identical*
code.

**How it bit.** During the `EURAG_RERANK_BATCH` A/B, batch=8 measured
`compound_hit` 67% against 100% at batch=64 — a clean-looking 33-point
regression. A repeat run of the same config gave 100%, and batch=12 then gave
67%. The golden set has only **3 compound cases**, so one knife-edge case
flipping is 33 points. A "safer" batch size was nearly shipped on noise.

**Do this.** Pin expansion before any retrieval A/B:

```bash
EURAG_HYDE_MODEL=none python -m core.evaluation.harness
```

A single-case delta is **not** attributable to a code change without it.

**Detail:** [DEVLOG 2026-08-09](DEVLOG.md).

### [DECISION] `EURAG_RERANK_BATCH` defaults to 8

**Choice.** Bound the cross-encoder's forward-pass batch at 8, rather than
leaving fastembed's default of 64.

**Why.** 64 is above every pool `hybrid_retriever` builds (`max(k*5, 30)`: 30 at
`top_k=6`, **60 on the escalation path** at `EURAG_ESCALATION_TOP_K=12`), so the
whole pool always went through in one forward pass. Measured on 60 real chunks:

| batch | rerank allocation | time |
|---|---|---|
| 64 | **1607 MB** | 2.27s |
| 16 | 605 MB | 2.58s |
| **8** | **284 MB** | 2.71s |

**5.7× less transient memory for +0.44s**, which is noise beside a multi-second
Claude call.

**Why it costs nothing.** Cross-encoder scores are per-pair independent, so
batching bounds memory without touching ranking. Verified: every harness metric
identical from batch 4 to 64 with expansion pinned off.

**Why not 16 or 4.** 16 still allocates 605 MB — more headroom than a 4 GB host
should be spending on one step. 4 buys little over 8 and adds latency.

**Watch out.** Raising `EURAG_ESCALATION_TOP_K` grows the pool and this peak
with it.

### [GOTCHA] Escalation is the memory maximum, not a normal query

**What.** Sizing was derived from idle (835 MB) and peak-while-answering
(1.83 GB). Neither is the ceiling: escalation widens retrieval to `top_k=12`, so
the cross-encoder sees 60 candidates instead of 30 and allocates roughly double.

**How it bit.** A follow-up question escalated in production and the api
container was OOM-killed on a 4 GB host. **Symptom: `HTTP 502` with no
traceback in the api log** — the process is `SIGKILL`ed, so there is nothing to
catch or log. Confirm with `dmesg -T | grep -i 'killed process'` and a jump in
`docker inspect ... .RestartCount`.

**Do this.** Size for the escalation path, not the common one.
[`DEPLOY.md` §4](DEPLOY.md) now measures both.

### [GOTCHA] A Linux deploy needs `chown -R 10001:10001 data/raw`

**What.** `data/raw/` is gitignored, so a fresh clone lacks it and Docker
creates the bind-mount source as `root:root`. The image runs as `USER eurag`
(uid 10001) and cannot write `data/raw/.seed.lock` — the seeder dies in ~5s with
`PermissionError` and `service_completed_successfully` correctly refuses to start
the API behind it.

**Why the rehearsal missed it.** Docker Desktop on macOS remaps bind-mount
ownership, so this failure mode **only exists on Linux**. The local prod
rehearsal validates behaviour, not permissions.

**Proper fix, not yet done.** Move the lock into `/app/var/.seed.lock` — the
`apivar` named volume is already owned by `eurag:eurag` and is shared across
replicas, so the flock keeps working with no host-side setup step.

### [DECISION] `replicas: 1` in the prod compose

**Choice.** Ship one API replica by default instead of two.

**Why.** Measured ~2.2 GB idle / **3.2 GB peak** at 2 replicas versus ~1.4 GB /
~2.4 GB at 1. The deploy target is a 4 GB box; two replicas leave almost nothing
for the OS and invite an OOM kill mid-answer.

**What it does not cost.** All state is shared (Postgres / Redis / Qdrant), so
this is a one-line change on a bigger host — the multi-instance design is intact
and still demonstrated.

### [DECISION] Bound the memory spike instead of resizing the VM

**Choice.** Fix the unbounded rerank batch and stay on `e2-medium` (4 GB), rather
than moving to `e2-standard-2` (8 GB).

**Why.** The app's real requirement is ~2.4 GB peak. 8 GB was proposed first
because GCP's `e2` ladder jumps 4 → 8 with nothing between — a convenient shape,
not a measured need. Sizing hardware around an unbounded allocation is how a
14 MB corpus ends up on a $50/month box. The batch fix removed the requirement
entirely.

### [DECISION] Live on GCP trial credit; Hetzner after it expires

**Choice.** `e2-medium`, Ubuntu 24.04, 30 GB disk, static IP, europe-west3, on
the $300 / 90-day trial. Post-credit target is Hetzner `CAX21` (~€7/month, ARM).

**Why.** Oracle Always Free was first choice and is out (signup rejected). Every
1 GB always-free VM (AWS `t3.micro`, Azure `B1S`, GCP `e2-micro`) OOMs on the
first question. ARM suits Hetzner because the original images were built
`linux/arm64` — note GCP `e2` is x86_64, so images are built **on the VM**.

### [DECISION] `EURAG_FREE_ANON_QUESTIONS` drops to 2 — supersedes "stays at 3"

**Choice.** Two free anonymous questions, not three. Default changed in
`core/config.py`, and the var is now passed through to the api container in
`docker-compose.prod.yml`.

**Why.** User's call, same day as the entry below. Two is still enough to show
what the product does (ask something, ask a follow-up) while cutting the
per-visitor exposure on the server's Anthropic key by a third — and the follow-up
is the more expensive path, since contextualisation adds a Haiku call on top of
the answer. Billing alerts are still unset (HANDOFF open item 2), so the number
is currently the *only* live cost control.

**The passthrough matters as much as the number.** The api service has no
`env_file`; it lists its environment explicitly. `EURAG_FREE_ANON_QUESTIONS` was
not in that list, so putting it in the VM's `.env` only fed compose
interpolation, never the container — the code default silently won and the
"reversible with one env var" claim below was false in prod. Now it is true.

**Reversible.** `EURAG_FREE_ANON_QUESTIONS` in the VM's `.env`; `0` makes the
deployment BYOK-only.

### [DECISION] ~~`EURAG_FREE_ANON_QUESTIONS` stays at 3~~ (superseded above)

**Choice.** Keep 3 free anonymous questions on a public URL, and control cost at
the source instead.

**Why.** It's a portfolio project — people trying it is the point. Turnstile now
blocks the bot floor that made this risky. The exposure is real (anonymous
answers are full-quality and escalation-enabled, billed to the *server's* key),
so the control is a spend limit on the Anthropic key plus GCP billing alerts,
not a smaller number.

**Reversible.** One env var; `0` makes the deployment BYOK-only.

---

## Before 2026-08-09

Not backfilled. Earlier decisions and gotchas live in their original homes:

- **Live-safety batch design decisions (D1–D17)** —
  [`../context_files/PLAN_LIVE_SAFETY.md`](../context_files/PLAN_LIVE_SAFETY.md)
- **Standing gotchas** (reseed after chunker changes, the double settings read in
  `api/main.py`, `.env` vs `.env.prod.local`, `EURAG_DOMAIN` at the compose
  layer, anon quota keyed per IP, `EURAG_TRUST_PROXY`) —
  [`../CLAUDE.md`](../CLAUDE.md) → Gotchas
- **Build history with measurements** — [`DEVLOG.md`](DEVLOG.md)
