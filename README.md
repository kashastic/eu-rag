# EURAG ★ — EU SME Intelligence Hub

[![CI](https://github.com/kashastic/eu-rag/actions/workflows/ci.yml/badge.svg)](https://github.com/kashastic/eu-rag/actions/workflows/ci.yml)

**Citation-first RAG over official EU law.** Ask compliance and funding questions
in plain language; get answers grounded in the actual regulations — GDPR, AI Act,
DSA, working time, VAT, product safety and 25+ more — where **every claim carries
a `[N]` citation that resolves to a real passage** with a link to the official
text on EUR-Lex. If the sources can't support an answer, EURAG says so instead
of guessing.

> ⚠️ Information, not legal advice. **Live at
> <https://eurag.duckdns.org>.** Runs local single-user out of the box;
> multi-user deployment is gated behind the optional security spine
> (auth, tenant isolation, PII gate, encryption, per-client rate limiting,
> prompt-injection fencing — see [SECURITY.md](docs/SECURITY.md)).

## What makes it interesting

- **Enforced citations** — answers whose `[N]` markers don't resolve to
  retrieved chunks are rejected and regenerated; if generation can't cite, it
  falls back to verbatim quotes. Nothing ships uncited.
- **Honest insufficiency** — the model must flag when the sources don't answer
  the question, and the UI shows it, rather than papering over gaps.
- **Cost-aware model cascade** — a cheap model (Claude Sonnet) answers
  everything; only low-confidence answers trigger one retry on a stronger
  model (Claude Opus) over deeper retrieval. You pay Opus prices only for the
  questions that need it.
- **Multi-user-ready security (optional)** — off by default for local use;
  flip `EURAG_AUTH_ENABLED=on` for JWT auth, per-user tenant isolation
  (enforced in one place, adversarially tested), a PII gate that rejects
  uploads containing personal data before they're embedded, AES-256-GCM
  at-rest encryption, an audit log that outlives the accounts it records
  (deleting an account pseudonymises its entries rather than erasing them, so
  erasure can't destroy evidence of an attack), self-service GDPR Art. 17
  erasure, per-client rate limiting, and prompt-injection defense (retrieved
  text is fenced as untrusted data, never instructions).
- **It refuses before it spends** — greetings and small talk are recognised
  before retrieval and answered from a constant: no search, no model call, and
  no free question spent. Retrieval ranks but never rejects, so without this
  "hello" came back with six passages of EU law and an instruction to answer
  from them.
- **Hybrid retrieval, measured** — BM25 + multilingual embeddings fused with
  RRF, reordered by a local cross-encoder reranker, capped per-document for
  citation diversity. Every retrieval change ships with before/after numbers
  from the built-in eval harness over 32 golden questions (with query
  expansion pinned off for repeatability: doc_hit 100%, MRR 1.00, phrase_hit
  90–94%; pin the follow-up rewriter off too and it is fully deterministic at
  94% / 0.94 / 87%). Anything with a model call in it makes the harness
  non-deterministic, so an A/B that doesn't pin them is measuring noise.
- **Verified corpus** — 31 EU acts from EUR-Lex plus EC portal pages, a
  live snapshot of open EU funding calls, and 10 national funding agencies —
  every page title-verified before ingestion (a wrong CELEX id can't
  silently ingest the wrong law) and link-checked (citations must never 404).
- **Production web app** — a Next.js frontend (`frontend/web/`): try it
  anonymously (a few free full-quality questions, server-metered), then a
  login wall; sign in with a username and password or with Google (ID-token
  flow, no client secret). Accounts get saved chats (new chat, history,
  rename, delete), and the anonymous thread you were mid-way through is
  adopted into the account rather than thrown away — the wall fires precisely
  when you have a conversation worth keeping. Logged-in free tier runs a cheap
  model; bring your own Anthropic key (encrypted at rest) to unlock the full
  cascade on your own bill. The backend runs multi-instance: Postgres for
  users/sessions/chat history, Redis for rate limits, Qdrant server for
  vectors, behind a single-origin Caddy proxy. See [DEPLOY.md](docs/DEPLOY.md).
- **Optional business context** — four closed-vocabulary fields (country, size
  band, sector, AI role) that tailor how an answer is framed. They reach the
  answerer only; retrieval stays blind, so the same question retrieves the
  same passages either way. Enums rather than free text on purpose: the
  sentence they build lands outside the untrusted-source fence.
- **No cookies, no analytics, no trackers** — the only device storage is the
  session token. Fonts are self-hosted, so a page view makes no third-party
  request at all, and published `/privacy` and `/terms` say plainly that
  questions are sent to a model provider in the US. Account erasure is
  self-service.
- **Works without an API key** — no key → extractive mode: answers are
  verbatim quotes from the retrieved passages. Zero hallucination risk,
  still cited.

## Quick start

Requires **Python 3.11+**.

```bash
git clone https://github.com/kashastic/eu-rag.git
cd eu-rag

# 1. Virtualenv + install
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Configure (optional — skip for keyless quote mode)
cp .env.example .env        # then edit .env, see below

# 3. Pull the corpus from EUR-Lex (~6 min: polite 10s crawl delay, then
#    local embedding — raw HTML is cached so this only fetches once)
python -m data.scrapers.eurlex

# 4. Run the API + chat UI
uvicorn api.main:app
# open http://localhost:8000
```

In a hurry? Skip step 3 and run `python -m data.seed` — you get a tiny
4-document sample corpus that works offline.

First run downloads two small ONNX models (multilingual embedder ~120 MB,
reranker ~80 MB); both run locally and cost nothing per query.

## Run with Docker

```bash
cp .env.example .env          # add ANTHROPIC_API_KEY (optional)
docker compose up             # → http://localhost:8000
```

The image is multi-stage, runs as a non-root user, has a healthcheck, and
seeds the corpus on first boot. State (registry, vectors, auth db) persists in
a named volume; mount a populated `data/raw/` for the full corpus, otherwise
the bundled samples are seeded. For multi-user, set `EURAG_AUTH_ENABLED=true`
and a strong `EURAG_JWT_SECRET` in `.env` before starting. For the full
horizontally-scalable stack (Postgres + Redis + Qdrant + web + reverse
proxy), see [docs/DEPLOY.md](docs/DEPLOY.md) and `docker-compose.prod.yml`.

## Setting up `.env`

Copy `.env.example` to `.env` (gitignored) and fill in what you need.
Real environment variables always override `.env` values.

| Variable | Default | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(unset)* | Enables LLM-written answers. Get one at [console.anthropic.com](https://console.anthropic.com/) → API keys. **Without it EURAG still works** in extractive quote mode. |
| `EURAG_LLM_MODEL` | `claude-sonnet-5` | Primary answer model ($3/$15 per MTok). `claude-haiku-4-5` is cheaper, `claude-opus-4-8` maximum quality. |
| `EURAG_ESCALATION_MODEL` | `claude-opus-4-8` | Consulted once when the primary answer is low-confidence. Set `none` to disable the cascade. |
| `EURAG_ESCALATION_TOP_K` | `12` | How many chunks the escalation retry retrieves. |
| `EURAG_RERANKER` | `Xenova/ms-marco-MiniLM-L-6-v2` | Local cross-encoder reranker (~1s/query on CPU, +6pp answer-passage precision). Set `none` to disable. |
| `EURAG_RERANK_BATCH` | `8` | Cross-encoder forward-pass size. A **memory ceiling, not a tuning knob**: fastembed's default of 64 exceeds every pool the retriever builds, so the whole pool went through in one pass and allocated 1.6 GB, which OOM-killed the container on a 4 GB host. Same scores at 8. |
| `EURAG_HYDE_MODEL` | `claude-haiku-4-5` | HyDE query expansion: a cheap model drafts a hypothetical regulation passage for the vector search. Lifts multi-topic questions 67%→100%. Set `none` to disable. |
| `EURAG_CONTEXTUALIZE_MODEL` | `claude-haiku-4-5` | Rewrites a follow-up into a standalone question before retrieval. Only fires when a request carries history. Set `none` to disable. |
| `EURAG_DECOMPOSE_MODEL` | `none` | Multi-hop query decomposition — measured, no gain over HyDE, so off by default. |
| `EURAG_EMBEDDER` | `fastembed` | `hash` = deterministic offline embedder (tests / cold start). |
| `EURAG_EMBED_MODEL` | `paraphrase-multilingual-MiniLM-L12-v2` | Any fastembed-supported model. |
| `EURAG_TOP_K` | `6` | Chunks retrieved per query. |
| `EURAG_DATA_DIR` | `var` | Where the registry (SQLite) and vectors (embedded Qdrant) live. |
| `QDRANT_URL` | *(unset)* | Point at a Qdrant server instead of embedded local mode. |

A typical `.env` is just one line:

```bash
ANTHROPIC_API_KEY=sk-ant-...
```

Running it multi-user adds auth, tiering, the bot gate, Google sign-in and the
shared Postgres/Redis/Qdrant settings. Those live in
[DEPLOY.md](docs/DEPLOY.md) rather than here — and note that production
secrets belong in a separate env file, never in `.env`, because
`pydantic-settings` reads `.env` during the test suite too.

**Cost expectations:** a normal question costs one Sonnet call (fractions of a
cent). A hard question additionally costs one Opus call. Retrieval, embedding,
and reranking are local and free.

## How it works

```
                        ┌──────────────────────────────────────────────┐
 EUR-Lex ──scraper──►   │  ingest: html→text → chunk per article       │
 (title-verified,       │  → embed (multilingual MiniLM, local ONNX)   │
  cached, rate-limited) │  → SQLite registry + embedded Qdrant         │
                        └──────────────────────────────────────────────┘
                        ┌──────────────────────────────────────────────┐
 question ─────────►    │  greeting? → canned reply, stop here         │
                        │  follow-up? → rewrite to stand alone (Haiku) │
                        │  retrieve: HyDE (Haiku) → BM25 + vector      │
                        │  search → RRF fusion                         │
                        │  → cross-encoder rerank (pool of 30+)        │
                        │  → max 2 chunks per document → top 6         │
                        └──────────────────────────────────────────────┘
                        ┌──────────────────────────────────────────────┐
                        │  answer (Claude Sonnet): cite-every-claim    │
                        │  prompt over numbered sources                │
                        │  → citation validation ([N] must resolve)    │
                        │  → low confidence? one retry on Claude Opus  │
                        │    over deeper retrieval (k=12, 6/doc)       │
                        └──────────────────────────────────────────────┘
                                     │
             answer + resolvable citations + escalated/insufficient flags
```

**Ingestion** ([`data/scrapers/eurlex.py`](data/scrapers/eurlex.py)) —
pulls each act as HTML from EUR-Lex, verifies the text against expected title
phrases before ingesting (EUR-Lex answers unknown ids with an HTTP 200 error
page, so status codes prove nothing), caches raw HTML under `data/raw/`
(never re-fetches), and respects `robots.txt` (10s crawl delay, identifying
User-Agent). Every document records provenance — title, source URL, fetch
time — and the loader rejects documents without it.

**Retrieval** ([`core/retrieval/`](core/retrieval)) — lexical matching is
load-bearing in legal text (regulation numbers and article references are
exact strings semantic search fumbles), so BM25 and vector results are fused
with Reciprocal Rank Fusion (the vector leg searches a HyDE-expanded query —
a cheap model drafts a hypothetical regulation passage to bridge the register
gap between questions and legal text), then a local cross-encoder scores
query/passage pairs jointly to promote the passage that actually answers the question.
Results are capped at 2 chunks per document — full regulations span hundreds
of chunks, and without the cap one dominant act crowds out the right answer.

**Generation** ([`core/generation/`](core/generation)) — the model may only
use the numbered sources it is given and must cite every claim. Validation
rejects uncited or mis-cited answers (one retry, then verbatim-quote
fallback). The model must append a structured marker when sources are
insufficient; that marker (or failed validation) triggers the Opus cascade.

**Evaluation** ([`core/evaluation/`](core/evaluation)) — a golden set of 32
questions with expected documents, expected verbatim passages, and
multi-document expectations for compound questions. Run it
yourself:

```bash
# pin expansion off, or the harness is non-deterministic and you measure noise
EURAG_HYDE_MODEL=none python -m core.evaluation.harness   # doc_hit@k, MRR, phrase_hit
python -m infra.scripts.check_links        # every citation URL must resolve
python -m pytest                           # 342 tests, fully offline
```

## The corpus

31 official EU acts (GDPR, ePrivacy, AI Act, DSA, DMA, NIS2, Data Act, CRA,
P2B, e-Commerce, DSM copyright, trade secrets, SME definition, late payment,
consumer rights, UCPD, unfair contract terms, sale of goods, digital content,
geo-blocking, product safety, product liability, services, working time,
transparent working conditions, pay transparency, VAT, VAT small-enterprise
scheme, CSRD, whistleblower protection, accessibility), EC SME-portal pages,
a refreshable snapshot of open Funding & Tenders grant calls, and one key
page per national funding agency (KfW, RVO, ICO, aws, Enterprise Ireland,
SNCI, EIFO, Almi, Business Finland, Invitalia — excerpts with link-out,
robots.txt enforced). Registry with licensing status:
[`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md).

EU legal texts are © European Union, reused under
[Decision 2011/833/EU](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32011D0833)
(reuse permitted with attribution). Only the versions published in the
Official Journal of the European Union are authentic.

## API

| Endpoint | What it does |
|---|---|
| `POST /query` | `{"question": "...", "profile": {...}, "history": [...]}` → answer, citations, `mode`, `escalated`, `insufficient` |
| `GET /documents` | corpus contents with provenance |
| `POST /ingest` | add your own document (provenance required) |
| `GET /healthz` | corpus size, active models, and the public runtime config the web app needs (Turnstile sitekey, Google client id) |

With auth enabled there is also `/auth/*` (register, login, refresh, Google),
`/conversations/*` (saved chats, and `import` to adopt an anonymous thread),
`/account` (profile, stored key, self-service deletion) and `/admin/*`.
`profile` is a closed vocabulary and an unknown value is a 422; the older
free-text `industry` field is still accepted but ignored.

## License

Code is [MIT](LICENSE). EU legal texts in the corpus are © European Union,
reused under Decision 2011/833/EU (see [The corpus](#the-corpus)).

## Project docs

Start at [`docs/WIKI.md`](docs/WIKI.md): architecture decisions
([`ARCHITECTURE.md`](docs/ARCHITECTURE.md)), milestone plan
([`PROJECT_PLAN.md`](docs/PROJECT_PLAN.md)), threat model
([`SECURITY.md`](docs/SECURITY.md)), data-source registry
([`DATA_SOURCES.md`](docs/DATA_SOURCES.md)), and a running devlog with
before/after numbers for every retrieval change
([`DEVLOG.md`](docs/DEVLOG.md)).
