# Architecture

## Overview

EURAG is a Python (FastAPI) backend with a pluggable RAG core and a thin web
frontend. Milestone 1 runs everything in one process with embedded storage;
later milestones split services out via Docker Compose without changing module
boundaries.

```
┌─────────────────────────────────────────────────────────────┐
│ frontend/  chat UI (static page → Next.js in M5)            │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP
┌──────────────────────────▼──────────────────────────────────┐
│ api/       FastAPI: /query /ingest /documents /healthz      │
├─────────────────────────────────────────────────────────────┤
│ core/                                                       │
│  ingestion/   loader → chunker → embedder                   │
│  retrieval/   BM25 + vector search → RRF fusion             │
│  generation/  llm_client (Claude | extractive) → citations  │
│  security/    (M3) auth, RBAC, tenant isolation, PII, erasure│
│  agents/      (M5) freshness orchestrator                   │
├─────────────────────────────────────────────────────────────┤
│ storage      Qdrant (embedded local mode → server via       │
│              Docker), SQLite doc registry (→ Postgres)      │
└─────────────────────────────────────────────────────────────┘
```

## Pipeline stages

### Ingestion (`core/ingestion/`)
- **`document_loader.py`** — normalizes HTML / plain text (PDF, DOCX in M4)
  into a `Document` with source metadata (title, url, source_type, language,
  fetched_at). Metadata captured at load time is what citations resolve to
  later, so nothing enters the pipeline without it.
- **`chunker.py`** — paragraph-aware chunking with a token budget and overlap.
  Legal texts have strong structure (articles, recitals); the chunker keeps
  article boundaries intact where possible because a citation to "half of
  Article 6" is useless.
- **`embedder.py`** — embedding abstraction. Default: FastEmbed ONNX
  (`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`).
  Fallback: deterministic hashing embedder
  (no model download) so tests and cold-start dev work offline. The interface
  is three methods; swapping models is a config change.

### Retrieval (`core/retrieval/`)
- **`bm25.py`** — in-memory BM25 (Okapi) over chunk tokens. Lexical matching
  matters in this domain: regulation numbers ("2016/679"), article references,
  and scheme names are exact strings semantic search reliably fumbles.
- **`vector_store.py`** — Qdrant wrapper. Embedded local mode (path on disk)
  now; the same client speaks to a Qdrant server when `QDRANT_URL` is set.
- **`hybrid_retriever.py`** — runs both, fuses with Reciprocal Rank Fusion.
  RRF over score normalization because BM25 and cosine scores live on
  incomparable scales; rank-based fusion needs no tuning to be robust.
  Results are capped at 2 chunks per document (full regulations span
  hundreds of chunks; without the cap one act monopolizes every slot).
- **`reranker.py`** — cross-encoder reranking (fastembed `TextCrossEncoder`,
  default `Xenova/ms-marco-MiniLM-L-6-v2`, `EURAG_RERANKER=none` disables).
  Fusion recall decides what is considered (pool of ≥30), the cross-encoder
  decides what wins. Shipped with before/after numbers from the harness
  (phrase_hit 82%→88% at doc_hit 100%; ~1s/query on CPU).
- **`expansion.py`** — HyDE (default ON, Haiku): a cheap model drafts a
  hypothetical regulation-style passage and the vector leg embeds
  question+passage; BM25 keeps the raw question so regulation numbers stay
  literal. Compound-question retrieval 67%→100%. Query decomposition lives
  in the same module but ships OFF — measured, no gain on top of HyDE.
- **`core/evaluation/`** — golden cases (`golden.py`, shared with tests) and
  the measurement harness (`python -m core.evaluation.harness`): doc_hit@k,
  doc MRR, and phrase_hit (does a retrieved chunk contain the verbatim
  passage that answers the question). Run before/after every retrieval
  change. HyDE and query decomposition remain M2 follow-ups.

### Generation (`core/generation/`)
- **`llm_client.py`** — provider abstraction. `AnthropicClient` when
  `ANTHROPIC_API_KEY` is set; `ExtractiveClient` otherwise (quotes the best
  retrieved passages verbatim — zero hallucination risk, still cited).
- **`citations.py`** — `CitationSchema`: every `[N]` in an answer maps to
  {chunk_id, document title, source URL, quoted span}. The API response
  carries the full mapping; the frontend renders citations as clickable chips.
- **`answerer.py`** — orchestrates retrieve → prompt → generate → validate
  citations. Answers whose `[N]` references don't resolve to retrieved chunks
  are rejected and regenerated once, then downgraded to extractive.

## Key decisions

| Decision | Choice | Why |
|---|---|---|
| Vector DB | Qdrant | Embedded local mode = zero-infra dev; native namespace isolation for multi-tenancy in M3 |
| Embeddings | multilingual-e5-small (ONNX via FastEmbed) | Multilingual (24 EU languages), small enough for laptop CPU, ONNX avoids the torch dependency |
| Fusion | RRF | Scale-free, no tuned weights, well-studied |
| LLM | Claude (Sonnet) default, abstracted | Provider abstraction protects against pricing/model churn; extractive fallback keeps the product demoable with no key |
| Doc registry | SQLite → Postgres | Same SQL, embedded now, server when Compose lands |
| Frontend | static page → Next.js 14 | Prove the API contract first; invest in UI once the contract is stable |

## Non-goals for Milestone 1
Auth, multi-tenancy, PII scanning, encryption, agentic web-freshness, live
scrapers at scale. Each is designed-for (module seams exist) but intentionally
not implemented yet — see [PROJECT_PLAN.md](PROJECT_PLAN.md).
