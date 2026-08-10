# LinkedIn — EURAG

Two pieces: the **post** (publish first, links to the article) and the
**article** itself. Facts checked against the repo on 2026-08-10 — 32 commits,
~6,100 lines of Python and ~3,100 of TypeScript, 322 tests, 47 documents live.

---

## THE POST

> Paste as-is. LinkedIn eats markdown, so the emphasis below is plain text on
> purpose. First two lines are what shows before "…see more" — they carry the hook.

I spent five weeks building an AI assistant for EU compliance, and the hardest
problem wasn't retrieval.

It was teaching it to say "I don't know."

Ask a general-purpose chatbot whether you need a Data Protection Officer and it
will tell you — confidently, fluently, and sometimes with an article number it
invented. For a 12-person company trying to work out what actually applies to
them, a confident wrong answer is worse than no answer at all.

So EURAG runs on one rule: every claim points to a numbered article in an
official text, or the claim doesn't get made. 47 documents — the GDPR, the AI
Act, the Late Payment Directive, 28 more EUR-Lex acts, and 10 national funding
agencies. If the corpus can't answer your question, it says so instead of
guessing.

Building that turned out to be mostly a lesson in restraint:

→ The refusal path was the hardest thing to get right. An honest "the sources
don't cover this" has no citations — and my citation validator rejected it,
regenerated it, and then downgraded it to quotes from the very passages it had
just declined to use. Four model calls to produce a worse answer than the one I
already had.

→ A fresh database cannot test a migration. One index in the wrong place took
the live API down at boot, and every test passed, because every test built its
schema from scratch.

→ A batch-size default I'd never looked at allocated 1.6GB in a single forward
pass and got the container OOM-killed in production. Symptom: 502s, no traceback.

→ Loading fonts from Google sent every visitor's IP to Google. On a privacy
compliance product. They're self-hosted now.

It's live, it's open source (MIT), and it is emphatically not legal advice — it's
a research tool that shows its work.

Full write-up in the article below: the retrieval pipeline, the cost cascade,
and the five production bugs that taught me more than the five weeks of building
did.

🔗 https://eurag.duckdns.org
💻 https://github.com/kashastic/eu-rag

#RAG #LLM #EUCompliance #GDPR #AIAct #SoftwareEngineering #OpenSource

---

## THE ARTICLE

### Title

**I spent five weeks teaching a RAG system to say "I don't know"**

### Subtitle

Building EURAG, a citation-first assistant for EU compliance and funding — and
what production taught me that my test suite couldn't.

---

### The problem is confidence, not knowledge

A small business owner in Germany wants to know whether they need a Data
Protection Officer. They have 30 employees. They ask a chatbot.

What they get back is fluent, structured and plausible. It may cite "Article 37
GDPR" correctly. It may cite an article number that doesn't exist. It may
confidently apply the 250-employee threshold from Article 30(5), which governs
something else entirely — records of processing, not DPOs. The answer will not
tell them which of those three things it just did, and all three read the same.

That last part is the actual problem. Not that language models get things wrong
— everything gets things wrong — but that the wrongness is invisible. A
compliance answer you cannot check is not a cheaper lawyer. It is a liability
with better grammar.

So I built EURAG around a single rule, and let everything else follow from it:

> **Every claim points to a numbered article in an official text, or the claim
> doesn't get made.**

Not "usually cites sources." Not "cites sources where possible." If the model
writes a sentence it cannot attach to a retrieved passage, the answer is
rejected before the user sees it. And if the corpus genuinely doesn't cover the
question, the correct output is to say so — not to produce something
well-written.

### What's actually in it

47 documents, all official, all fetched from primary sources rather than
scraped summaries:

- **31 EUR-Lex acts** — GDPR, the AI Act (Reg. 2024/1689), NIS2, CSRD, the Data
  Act, the Cyber Resilience Act, Late Payment, consumer rights, working time,
  pay transparency, the VAT small-enterprise scheme, and more.
- **European Commission portal pages** and a snapshot of currently open Funding
  & Tenders calls.
- **10 national funding agencies** across member states, stored as excerpts with
  links out rather than mirrored wholesale — they're someone else's content and
  their robots.txt gets respected.

That becomes roughly 4,200 retrievable chunks. The chunker is article-aware:
headings are hard boundaries, and every chunk carries its own "Article N —"
heading into the index. This sounds like a detail. It is the difference between
retrieving "the paragraph about employee thresholds" and retrieving "Article
30(5), which is about records of processing and mentions a threshold."

### How a question gets answered

The pipeline has more steps than a demo needs, and each one earned its place by
fixing something measurable:

1. **Contextualise the follow-up.** "What if I have 29 people?" has no topic of
   its own. When there's conversation history, a cheap model first rewrites the
   question so it stands alone. This runs *before* everything else — expanding a
   fragment just amplifies the wrong topic.
2. **Expand with HyDE.** Generate a hypothetical answer and embed that instead
   of the raw question, because legal text looks more like an answer than like a
   question.
3. **Search twice.** BM25 for exact terms — regulation numbers and legal phrases
   must stay literal — and dense vectors for meaning. Fused with Reciprocal Rank
   Fusion.
4. **Rerank.** A cross-encoder reorders the candidate pool.
5. **Cap per document.** No more than two chunks from any one act on the first
   pass, so a single verbose regulation can't crowd out the one that actually
   answers the question.
6. **Generate under the citation rule**, then validate that every marker
   resolves to a real chunk.

Retrieval quality is measured, not vibed: a golden set of 32 cases tracks
document hit rate, mean reciprocal rank, and whether the answer contains the
specific phrase that settles the question. Document hit rate is 100%; phrase
precision sits at 94%.

One hard-won note on that harness: **HyDE makes it non-deterministic.**
Expansion calls a model, so every run produces a different candidate pool. With
only a handful of compound cases in the set, one flip moves a metric by 33
points. I nearly shipped a "safer" configuration on pure noise. Every A/B now
pins expansion off before measuring anything.

### The refusal was the hardest part

Here is the bug I'm most glad I found, because it is invisible until you look at
the token bill.

The system prompt tells the model: if the sources don't answer the question, say
so plainly and end your reply with `INSUFFICIENT_SOURCES`. The validator says:
every answer must contain at least one citation.

You can see the collision. An honest refusal *has no citations* — that's what
makes it a refusal. So the validator rejected it. Regenerated it. The model,
being consistent, refused again. Rejected again. Then the system fell back to
"extractive mode" and shipped verbatim quotes from the very passages the model
had just correctly said were irrelevant.

Four model calls, ending in a worse answer than the first one. The guardrail was
punishing the model for obeying its own instructions.

The fix is narrow on purpose: an uncited answer is valid **only** if it carries
the insufficiency marker and is short — under 600 characters. A refusal is a
sentence or two. A long uncited body is a substantive answer with a marker
tacked on, and shipping that is exactly the unsourced claim the whole discipline
exists to prevent. Four calls became two.

I only found it because I added per-query telemetry to measure something else
entirely. Fixing the measurement exposed the bug.

### Making it affordable

A public URL that spends your API key on every visitor is a bad night's sleep
waiting to happen. Cost control is structural:

- **An escalation cascade.** Every question starts on a cheap model. Only if the
  answer comes back low-confidence — the insufficiency marker, or failed
  citation validation — does it retry on a stronger model over deeper retrieval
  (more chunks, and a relaxed per-document cap, because insufficiency usually
  means the right document was found but the right passage sat below the cap).
- **Tiers.** Anonymous visitors get a small number of full-quality questions,
  metered server-side by IP. Then a login wall. Logged-in free accounts get a
  cheaper model and a lifetime allowance. Past that, bring your own API key —
  encrypted at rest, billed to you.
- **Telemetry designed to be greppable.** Exactly one unconditional log line per
  query, because a rate needs a denominator and every other line I had fired
  only on a branch. It's ASCII-only for a stupid, real reason: an earlier attempt
  to count escalations failed on an em dash in the log string.

I'll be honest about the limit of this: I have not yet read that telemetry
against meaningful real traffic. The instrument exists; the reading doesn't.
Which is why the next thing I build is a relevance floor — right now nothing
checks whether the best retrieved chunk is *actually relevant*, so an
off-corpus question rides the full expensive cascade to produce a refusal that
could have cost nothing.

### Five things production taught me that tests didn't

**1. A fresh database cannot test a migration.**
I added Google sign-in, which needed a unique index on a new column. I put the
index in the schema block — which runs *before* the statements that add the
column. Every test passed, because every test builds its database from scratch,
where the column already exists in the CREATE TABLE. Every *existing* database
raised at boot. The API never started.

Worse on Postgres, where a multi-statement script is executed as one statement:
a single bad line discards the entire schema. The symptom the user sees is not
an error page. It's the UI reporting "0 documents indexed," because the frontend
reads its document count, its bot-gate key and its sign-in configuration from
one health endpoint, and a dead endpoint blanks all three at once.

**2. Never gate your primary action on a third-party widget.**
The bot-check sat visibly above the composer and disabled both "Ask" and "Create
account" until it solved. If a privacy extension blocked the challenge script —
or if the site key didn't match the domain — both buttons were simply dead. No
error, no explanation. It's invisible now and runs at submit time, and nothing
waits on it.

**3. A default you never looked at is a decision you never made.**
The reranker's batch size defaulted to 64, which was larger than any candidate
pool the retriever ever built. So the entire pool went through in a single
forward pass and allocated 1.6GB transiently. The container got OOM-killed:
502s, no traceback, nothing in the application logs at all. Setting it to 8 gave
identical scores for 5.7× less peak memory and 0.44 seconds.

**4. A rewrite step inherits the promises of whatever it feeds.**
The answerer promises to reply in the language the question was asked in. I
added a step that rewrites follow-ups into standalone questions — and it
sometimes translated them. The answerer then kept its promise faithfully against
the *rewrite*, so an English question came back answered in Spanish. If you put
a transformation in front of a component, re-read that component's contract and
carry every input-shaped promise into the new step.

**5. Convenience has a privacy cost you have to go looking for.**
The site loaded three typefaces from Google Fonts, which means Google received
every visitor's IP address on every page view — on a product about EU
compliance. They're self-hosted now: 26 subsetted font files, no third-party
request on a page load at all. You can verify it with curl in one line, which is
the point.

### Honesty as a design constraint

This is the part I'd most want another engineer to take away.

The question that kicked it off was "why don't we have a cookie banner?" I
audited it expecting to add one, and the audit answered in reverse: the app sets
no cookies, runs no analytics, and carries no tracker. The only thing stored on
your device is your session token, which ePrivacy exempts as strictly necessary.
A consent banner would be *managing consent that isn't being collected* — on a
compliance product. So there deliberately isn't one.

The real gap wasn't consent. It was transparency: nothing on the site said that
every question is sent to a model provider in the US. Now `/privacy` and
`/terms` say exactly that, along with what's stored, for how long, and how to
delete it — and there's a self-service account erasure endpoint behind it that
actually works.

And the thing I will not write anywhere in the product copy: **"GDPR
compliant."** That's a conclusion about an organisation, not a property of a
codebase, and the organisational pieces genuinely aren't there — no legal entity
named, no processor agreement with the model provider, no lawyer has read the
notice against the real processing. What the site *can* stand behind is the
claim you can check yourself: no cookies, no tracking, no analytics.

Being able to say precisely what you haven't done is worth more than a badge.

### What it deliberately doesn't do

- It doesn't give legal advice, and it says so on every screen.
- It doesn't generate compliance checklists or readiness scores. That converts
  "here is the text and where it came from" into "here is what you must do,"
  which is the line I'm not qualified to cross.
- It doesn't guess your country from your IP. Geolocating visitors to silently
  change a legal answer is exactly the kind of processing the privacy notice
  says doesn't happen.
- It doesn't pretend the corpus is complete. 47 documents is a useful slice of
  cross-sector EU law, not all of it, and the system is built to say so.

### The stack, briefly

FastAPI and Python on the backend; Next.js and React on the frontend. Qdrant for
vectors, a dialect layer that runs SQLite locally and Postgres in production,
Redis for distributed rate limiting, Caddy terminating TLS on a single origin.
Docker Compose with a one-shot seeder that populates shared volumes before any
API replica starts, so replicas never scrape or re-embed. It runs on a 2 vCPU /
4 GB VM.

322 tests, and they run fully offline — a deterministic hash embedder stands in
for the real model, so the whole suite passes with no API key and no network.
That was worth every hour it cost.

### Where it goes next

Billing alerts, which is the one genuinely unbounded risk and the least
interesting thing to build. A relevance floor to stop off-corpus questions from
reaching a model at all. More member-state coverage. And reading that escalation
telemetry against real traffic before tuning anything based on how I *imagine*
people use it.

If you run a small business in the EU, or you build retrieval systems and want
to argue with my design choices, I'd genuinely like to hear it.

**Try it:** https://eurag.duckdns.org
**Read the code:** https://github.com/kashastic/eu-rag (MIT)

*EURAG provides information, not legal advice. For binding advice, talk to a
qualified professional.*
