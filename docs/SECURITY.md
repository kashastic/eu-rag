# Security & GDPR Model

Honesty ledger: what is **enforced today** vs **designed-for** (seams exist,
implementation lands in M3). Do not deploy multi-user before M3 completes.

## Enforced today (M1)
- No personal data in corpus: seed corpus is official public documents only
- Provenance on every chunk (audit trail starts at ingestion)
- Extractive fallback = zero-hallucination mode when no LLM key present
- Citation validation: answers with unresolvable `[N]` refs are rejected

## Designed-for, lands in M3
| Control | Design |
|---|---|
| AuthN | JWT, short-lived access + refresh rotation, revocation list |
| AuthZ | RBAC decorators on routes (`admin`, `member`, `viewer`) |
| Tenant isolation | Qdrant namespace per tenant + registry row scoping; injected once via FastAPI dependency, tested adversarially (`tests/security/test_isolation.py`) |
| PII gate | Presidio scan in the ingestion pipeline, BEFORE embedding — vectors are irreversible-ish but leaky; nothing personal may reach the embedder |
| At-rest encryption | AES-256-GCM for stored source documents, per-tenant keys |
| Audit log | Append-only, hash-chained, every query/ingest/erasure event |
| Erasure (GDPR Art. 17) | Deletes registry rows + vectors + raw cache; audit entry records the erasure, not the content |
| Prompt injection | Retrieved text is data, never instructions: system prompt hardening + injection test suite (M6) |

## Threat model (working notes)
- **Cross-tenant leakage** is the kill-shot risk for a compliance product →
  isolation is enforced in exactly one code path, never per-route.
- **Poisoned corpus** (malicious doc steering answers): provenance rules +
  only allowlisted scrapers write to the corpus.
- **LLM exfiltration via crafted questions**: generator sees only retrieved
  chunks for the caller's tenant; no tool access from the answer path (agentic
  layer in M5 gets its own sandboxed tool policy).

## Breach scenarios & how each is avoided

Ranked by damage to the product's core promise (GDPR-safe compliance answers).

| # | Breach scenario | How it would happen | How we avoid it | Lands |
|---|---|---|---|---|
| 1 | Tenant A reads Tenant B's documents | A missed tenant filter on one route or one Qdrant query | Isolation lives in ONE place (a FastAPI dependency that scopes every registry + vector call); adversarial tests try to cross tenants on every route | M3 |
| 2 | Personal data leaks via embeddings | PII embedded into vectors; vectors partially invertible; erasure misses them | Presidio PII gate runs BEFORE the embedder — flagged text never reaches it. Until M3, only official public documents are ingested (rule, enforced by loader `source_type` allowlist) | M3 (interim rule active now) |
| 3 | Erasure request doesn't actually erase | Doc deleted from registry but vectors / raw cache survive | Art. 17 erasure deletes registry rows + vector points (by `doc_id` payload filter, already indexed today) + raw cache in one transaction-like sweep; audit log records the event, never the content | M3 |
| 4 | Prompt injection: a scraped page contains "ignore instructions, reveal X" | Malicious/compromised source page enters corpus, LLM obeys it | Retrieved text is framed as numbered *data* sources, never as instructions; system prompt hardening; injection test suite runs in CI; answer path has zero tool access | M6 tests (prompt framing active now) |
| 5 | Hallucinated legal claim harms an SME | LLM invents an article number or deadline | Citation enforcement (active today): every claim must carry `[N]` resolving to a retrieved chunk, else the answer is regenerated then downgraded to verbatim quotes; "not legal advice" in every system prompt | Active now |
| 6 | Stolen/replayed auth token | Long-lived token leaks from a client | Short-lived access tokens + refresh rotation + server-side revocation list | M3 |
| 7 | Corpus poisoning via open ingest | Anyone POSTs a fake "regulation" to /ingest | /ingest requires auth from M3; until then the deployment is single-user local only (stated in README); scrapers are allowlisted writers | M3 |
| 8 | Legal exposure from scraping | Aggressive scraping of national portals against ToS | EUR-Lex/EC content is licensed for reuse (Decision 2011/833/EU); national scrapers (KfW/BPI) respect robots.txt, rate-limit, identify themselves, store excerpts + link out, and ship disabled by default | M4 |
| 9 | Secrets in the repo | API key committed | `.env` is gitignored; config reads env vars only; no key material in code or docs | Active now |

**Deployment rule until M3 ships:** run EURAG locally, single user, official
public documents only. Multi-user deployment before the security spine exists
is the one thing this plan forbids.
