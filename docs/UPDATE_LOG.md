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

## 2026-08-09

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

### [DECISION] `EURAG_FREE_ANON_QUESTIONS` stays at 3

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
