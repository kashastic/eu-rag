# Security & GDPR Model

Honesty ledger: what is **enforced today** vs still **designed-for**. The M3
security spine landed 2026-07-06 — the controls below are implemented and
covered by adversarial tests (`tests/test_security.py`, `tests/unit/test_auth.py`,
`test_crypto.py`, `test_pii.py`).

## Enforced today
- **Tenant isolation** — every document belongs to a tenant (shared official
  corpus = `public`; each user gets a private tenant). Reads are scoped to an
  allowed-tenant set derived in ONE place (`api/deps.py::allowed_tenants`);
  `Registry.get_chunks` is the hard gate that drops any chunk id outside them,
  so an upstream retrieval leak cannot surface another tenant's text. The
  vector store filters by tenant server-side as a second layer.
- **AuthN** — HS256 JWTs. Short-lived access tokens (15 min) carry
  sub/role/tenant; refresh tokens are single-use (rotated on use, tracked by
  jti) so a stolen refresh token dies on first reuse. scrypt password hashing.
- **AuthZ** — roles (`admin` / `user`); the first registered user is admin.
  Admin-only routes gated by a `require_admin` dependency.
- **PII gate** — scans uploads BEFORE chunking or embedding; on detection the
  document is REJECTED (not silently redacted — the uploader fixes it), so
  personal data never reaches the embedder or vector store. Official sources
  (our own verified scrapers) are exempt. Regex/Luhn backend by default;
  Presidio (NER) optional.
- **At-rest encryption** — AES-256-GCM for chunk text when
  `EURAG_ENCRYPTION_KEY` is set, transparent at the registry boundary.
  Version-prefixed ciphertext so plaintext and encrypted rows coexist.
- **Audit log** — append-only (SQLite triggers block UPDATE/DELETE), records
  who/what/when for register, login, query, ingest, PII rejection, erasure.
  Question texts are stored as SHA-256 hashes, never plaintext.
- **Erasure (GDPR Art. 17)** — deletes registry rows + vector points + live
  BM25 entries; per-document (owner or admin) or whole-tenant (admin, for
  account deletion). Audit records the event, not the content.
- No personal data in the official corpus; provenance on every chunk;
  citation validation; extractive zero-hallucination fallback.

Auth is **off by default** (`EURAG_AUTH_ENABLED` unset) so the local
single-user experience is unchanged — no tokens, one built-in admin over the
public corpus. Turning it on is what makes multi-user deployment safe.

## API-cost abuse model (access tiers)

The hosted deployment lets anyone try the product without an account, which is
also the surface a malicious user would use to burn the owner's Anthropic
credits. Defense:

- **Anonymous tier**: `EURAG_FREE_ANON_QUESTIONS` (default 3) full-quality
  questions, **counted server-side per client IP per day** (`core/quota.py`, on
  the shared DB — the browser popup only reflects this, it never enforces it).
  Spent → 401 `anonymous_limit_reached` → login wall.
- **Logged-in free tier**: answers on a cheap model (`EURAG_FREE_MODEL`, Haiku)
  with the Opus escalation **disabled** — bounds per-question cost.
- **BYOK**: a user stores their own Anthropic key (AES-256-GCM encrypted, never
  logged or returned); their requests use the full cascade billed **to them**.
- **Rate limiting** (Redis-shared) caps request bursts on top of the above.

Residual risk (accepted, no global $ ceiling by product choice): an attacker
rotating many IPs can still get 3 full-quality questions each. Mitigation is a
CAPTCHA (**Cloudflare Turnstile**) at the anonymous boundary — a clean seam
exists; add the site key to enable. Documented in `docs/DEPLOY.md`.

## Still designed-for
| Control | Status |
|---|---|
| Hash-chained audit (tamper-evident, not just append-only) | future hardening |
| Per-tenant encryption keys | single key today; per-tenant is a KMS swap |
| Prompt-injection test suite | M6 (prompt framing active now) |
| Rate limiting / abuse controls | M6 |

## Threat model
- **Cross-tenant leakage** is the kill-shot risk for a compliance product →
  isolation is enforced in exactly one code path, tested adversarially
  (`tests/test_security.py`: an attacker who knows another tenant's chunk id
  still gets nothing).
- **Poisoned corpus**: `/ingest` requires auth when enabled; uploads land in
  the uploader's private tenant, never `public`; official texts are seeded
  offline by allowlisted scrapers only.
- **PII exfiltration**: the gate runs before the embedder; erasure reaches
  vectors and BM25, not just the registry.
- **LLM exfiltration via crafted questions**: the generator sees only chunks
  for the caller's tenants; no tool access from the answer path.

## Breach scenarios & how each is handled

| # | Breach scenario | How avoided | Status |
|---|---|---|---|
| 1 | Tenant A reads Tenant B's documents | Isolation in ONE place (`allowed_tenants` → tenant-scoped `get_chunks` + vector filter); adversarial tests cross tenants three ways | ✅ enforced |
| 2 | Personal data leaks via embeddings | PII gate rejects flagged uploads BEFORE the embedder; official sources exempt | ✅ enforced |
| 3 | Erasure doesn't actually erase | Art. 17 sweep deletes registry + vector points + BM25 entries; idempotent; audited | ✅ enforced |
| 4 | Prompt injection from a scraped page | Retrieved text framed as numbered *data*, never instructions; answer path has zero tools | prompt framing active; test suite M6 |
| 5 | Hallucinated legal claim | Citation enforcement: every `[N]` must resolve or the answer is regenerated/downgraded; "not legal advice" always | ✅ active |
| 6 | Stolen/replayed auth token | Short-lived access tokens + single-use refresh rotation (jti revocation) | ✅ enforced |
| 7 | Corpus poisoning via open ingest | `/ingest` requires auth when enabled; uploads isolated to the uploader's tenant | ✅ enforced |
| 8 | Legal exposure from scraping | EUR-Lex/EC licensed for reuse (Decision 2011/833/EU); national scrapers respect robots.txt, rate-limit, identify, store excerpts + link out, opt-in per country | ✅ enforced |
| 9 | Secrets in the repo | `.env` gitignored; config reads env only; JWT secret + encryption key are env-provided | ✅ active |

**Deployment note:** with `EURAG_AUTH_ENABLED=true`, `EURAG_JWT_SECRET` set,
and `EURAG_ENCRYPTION_KEY` set, EURAG is safe to run multi-user. Left off, it
is a local single-user tool. Remaining pre-production items (rate limiting,
prompt-injection CI, load testing) are M6.
