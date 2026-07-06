# Project Plan

Six milestones, each ending in something runnable. Vertical slices over
horizontal layers: there is a working product after Milestone 1 and every
milestone after it makes the same product better.

## M1 — Walking skeleton ✅ (in progress → this repo)
End-to-end path with real structure:
- [x] Repo scaffold, wiki, git
- [x] Ingestion: loader (HTML/text) → paragraph-aware chunker → embedder
      (FastEmbed multilingual-e5, hashing fallback)
- [x] Storage: Qdrant embedded + SQLite document registry
- [x] Retrieval: BM25 + vector + RRF hybrid
- [x] Generation: Claude client with extractive fallback, enforced citations
- [x] API: `POST /query`, `POST /ingest`, `GET /documents`, `GET /healthz`
- [x] Chat UI (static page served by the API)
- [x] Bundled seed corpus (GDPR + EU SME funding excerpts), `python -m data.seed`
- [x] Unit tests for chunker, citations, fusion; end-to-end smoke test

**Done means:** clone → install → seed → ask "Do I need a DPO for a 30-person
company?" → cited answer in the browser.

## M2 — Retrieval quality ✅ (2026-07-06)
- [x] Evaluation harness + golden set (25 cases: doc/phrase/compound
      metrics; `python -m core.evaluation.harness`) — built FIRST
- [x] Cross-encoder reranker (ms-marco MiniLM; phrase_hit +6pp) — merged
      with before/after numbers
- [x] HyDE (Haiku; compound-question retrieval 67%→100%) — merged with
      before/after numbers
- [x] Multi-hop query decomposition — built and measured: no gain on top of
      HyDE (the reranker neutralizes sub-query candidates), ships
      config-gated OFF (`EURAG_DECOMPOSE_MODEL`)
- [x] Article-aware chunking (heading-stamped chunks, 320-word budget fits
      77% of articles whole; doc_mrr 1.00, fixed the GDPR Art. 37 miss)
- Final M2 numbers (k=6, live corpus): doc_hit 100%, doc_mrr 1.00,
  phrase_hit 92%, compound_hit 100%

## M3 — Security spine ✅ (2026-07-06)
- [x] JWT auth (HS256, 15-min access + single-use refresh rotation via jti);
      scrypt passwords; first user = admin, RBAC via `require_admin` dep
- [x] Multi-tenant isolation: registry row scoping + Qdrant tenant filter,
      derived once in `api/deps.py::allowed_tenants`, enforced at the
      `get_chunks` gate, tested adversarially (`tests/test_security.py`)
- [x] PII gate in ingestion — runs BEFORE embedding, rejects uploads with
      personal data, exempts official sources (regex/Luhn; Presidio optional)
- [x] AES-256-GCM at-rest encryption of chunk text (`EURAG_ENCRYPTION_KEY`),
      transparent at the registry boundary, version-prefixed
- [x] Append-only audit log (SQLite triggers); GDPR Art. 17 erasure across
      registry + vectors + BM25, per-document and per-tenant
- All off by default → local single-user mode unchanged. 141 tests.

## M4 — Data at scale (mostly done early, alongside M2)
- [x] EUR-Lex scraper — 31 acts, title-verified (`data/scrapers/eurlex.py`)
- [x] EC SME portal scraper (3 pages) + Funding & Tenders open-calls
      snapshot via SEDIA API (`data/scrapers/portals.py`, `funding_calls.py`)
- [x] National schemes: 10 countries pulled (KfW, RVO, ICO, aws, Enterprise
      Ireland, SNCI, EIFO, Almi, Business Finland, Invitalia) — excerpt +
      link-out policy, robots-enforced. Bpifrance/VLAIO blocked (403).
- [x] Incremental re-ingestion via content hashing
- [ ] PDF/DOCX loaders
- [ ] Postgres + Qdrant server via Docker Compose

## M5 — Agentic layer + real frontend
- LangGraph freshness orchestrator: detect stale/temporal questions → live
  web lookup → answer merges corpus + web with distinct citation types
- Next.js 14 + shadcn/ui + i18n (EN/DE/FR first), login flow, streaming answers

## M6 — Hardening & release
- Security test suites: prompt injection, tenant isolation
- Load testing; monitoring; staging/prod deploy workflows
- v1.0.0 tagged release with self-host docker-compose package

## Standing decisions
- Default LLM: Claude Sonnet via `ANTHROPIC_API_KEY` (abstracted in
  `core/generation/llm_client.py`)
- Embeddings run locally (ONNX); an API embedder is a config swap if needed
- Migrations (once Postgres lands): add-only, never DROP COLUMN
