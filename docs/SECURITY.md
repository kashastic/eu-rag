# Security & GDPR Model

Honesty ledger: what is **enforced today** vs still **designed-for**. The M3
security spine landed 2026-07-06 — the controls below are implemented and
covered by adversarial tests (`tests/test_security.py`, `tests/unit/test_auth.py`,
`test_crypto.py`, `test_pii.py`). The abuse, failure-handling, boot-guard and
client-IP sections were added by the "live safely" batch (2026-07-25 → 08-08)
and verified against a running production stack, not only in unit tests.

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

- **Anonymous tier**: `EURAG_FREE_ANON_QUESTIONS` (default 2) full-quality
  questions, **counted server-side per client IP per day** (`core/quota.py`, on
  the shared DB — the browser popup only reflects this, it never enforces it).
  Spent → 401 `anonymous_limit_reached` → login wall.
- **Logged-in free tier**: answers on a cheap model (`EURAG_FREE_MODEL`, Haiku)
  with the Opus escalation **disabled** — bounds per-question cost — and capped
  at `EURAG_FREE_USER_QUESTIONS` (default 10) questions **for the lifetime of
  the account**, not per day (`core.quota.UserQuota`). This is the piece that
  stops the server's key paying for a returning free user indefinitely. Spent →
  **402** `free_limit_reached`, and the only way on is BYOK. Deliberately not
  401: the web client treats 401 as "refresh the session token" and would loop
  (same trap as `byok_key_rejected`). Enforced in `deps.spend_free_question`,
  called by **both** logged-in ask paths — `/query` and
  `/conversations/{id}/messages` — because a gate on one of two doors is not a
  gate.
- **BYOK**: a user stores their own Anthropic key (AES-256-GCM encrypted, never
  logged or returned); their requests use the full cascade billed **to them**,
  and skip the free-tier counter entirely (frozen, not lost — removing the key
  returns whatever allowance was left).
- **Rate limiting** (Redis-shared) caps request bursts on top of the above.
- **Cloudflare Turnstile** (implemented 2026-07-25) gates anonymous questions
  **and registration** whenever `EURAG_TURNSTILE_SECRET` is set. Unset = off, so
  local single-user mode is untouched.

Residual risk (accepted, no global $ ceiling by product choice): a human
rotating IPs can still get `EURAG_FREE_ANON_QUESTIONS` full-quality questions
per address. Turnstile raises the cost of *automating* that; it does not
eliminate it. Set `EURAG_FREE_ANON_QUESTIONS=0` for a BYOK-only deployment
where the server's key is never spent on strangers.

### Google Sign-In: what is actually checked

The ID-token flow means the browser hands us a JWT and the server decides
whether to believe it, so `core/security/google_oauth.verify_id_token` **is** the
authentication boundary. It pins RS256 (never the token's own `alg`), verifies
the signature against Google's JWKS, requires `iss` to be Google, enforces
`exp`/`iat`, requires `email_verified`, and requires **`aud` to equal our own
client id** — without that last check any correctly-signed Google token, minted
for any app, would be a login here. It fails **closed** on an unreachable JWKS
(unlike Turnstile, which fails open — that one is bounded by the per-IP quota;
this one mints a session).

There is no client secret to leak: `EURAG_GOOGLE_CLIENT_ID` is public by design.

**Identity is keyed on `google_sub` only.** Never username, never email. Matching
on either would let someone register a username (or an account with a known
address) and inherit the real owner's account — chats and stored API key
included — the first time they sign in with Google. A derived username that is
taken is skipped instead. Google accounts carry an empty `pw_hash` and
`authenticate()` refuses password login on them outright.

### BYOK: what encrypting the user's key does and does not protect against

Users are asked to hand over an Anthropic API key, so be precise about the
guarantee — the UI now states this verbatim rather than implying more.

**What holds.** TLS in transit; AES-256-GCM at rest in `users.byok_key_enc`;
never returned to the client (`/account` exposes a `has_api_key` boolean and an
`api_key_set_at` timestamp, never the value); never written to logs or the audit
trail (the audit row records the *event*, `account.byok_set`); `DELETE
/account/api-key` genuinely clears it; format-checked before storage.

**What does not.** `EURAG_ENCRYPTION_KEY` lives in the environment of the same
host as the database. So encryption-at-rest defends against a **stolen DB dump,
a leaked backup, or a Postgres-only compromise** — not against root on that box,
not against the operator, and not against a future code change that logs the
plaintext. The key is also decrypted into process memory on *every* query
(`deps.paid_tier`), and an Anthropic key cannot be scoped to one application: it
can spend the user's whole balance anywhere.

**Therefore the mitigation is procedural, and the UI must carry it**: tell the
user to create a *dedicated* key with a spend limit, never their main key, and
to revoke it at Anthropic when done. Removing a key here does **not** revoke it
upstream — the Settings dialog says so, and nudges a rotation once a stored key
passes 30 days (`api_key_set_at`). Treat any change that weakens this copy as a
security regression.

### Turnstile: where it sits and how it fails

The check runs **before `anon_quota.consume`**, so a rejected token costs the
visitor nothing — a failed challenge must not burn a free question. It also
covers `/auth/register`, because a bot that can sign up bypasses the anonymous
quota entirely and gets server-key answers on the logged-in free tier.

Failure behaviour is deliberately asymmetric:

| Condition | Result | Why |
|---|---|---|
| No token supplied | **reject**, no network call | Nothing to verify; don't pay a round-trip to say no |
| Cloudflare says `success: false` | **reject** | The explicit negative is trustworthy |
| Cloudflare unreachable / times out | **allow**, log a warning | A Cloudflare outage must not take the product down. The per-IP quota and the rate limiter still hold, so failing open degrades to *exactly the pre-Turnstile posture* rather than to "unlimited" |

The sitekey is served from `/healthz` rather than baked in at build time, so
rotating keys is an env change and never a rebuild. **The universal test keys
pass everything** — a deployment still carrying them has no bot protection.

### Client IP is a trust decision (`EURAG_TRUST_PROXY`)

The anonymous quota and the rate limiter both key on "the client IP", resolved
in one helper (`api/deps.peer_ip`). `X-Forwarded-For` is client-settable, so
believing it on a directly reachable API hands anyone an unlimited supply of
fresh quota keys — one forged header per free question. The flag therefore
defaults to **off** (key on the peer address) and is set to `true` only in the
prod compose, where nothing but Caddy can reach the API.

This was originally a latent hole: the rate limiter was the documented concern,
but `client_ip` — the *quota* key — trusted the header unconditionally. Both now
share the guarded helper.

**Verified, not assumed** (2026-07-25, against the real stack): a request
carrying a forged `X-Forwarded-For` did **not** create a new quota bucket. Caddy
replaces the client-supplied header, so the first hop is genuinely the peer.

⚠️ **This breaks if you put Cloudflare's proxy (orange cloud) in front of
Caddy** — the peer becomes a Cloudflare edge IP and every visitor collapses into
a handful of shared quota and rate-limit buckets. Either configure Caddy's
`trusted_proxies` with Cloudflare's ranges (or key off `CF-Connecting-IP`), or
run Cloudflare DNS-only. Turnstile does not require Cloudflare to be in the
traffic path.

## Failure handling (availability + not leaking internals)

LLM-call failures used to surface as raw 500s *and* silently cost an anonymous
visitor a free question. Now:

| Failure | Response | Notes |
|---|---|---|
| BYOK key rejected by Anthropic | **400 `byok_key_rejected`** | Deliberately *not* 401 — the web client treats 401 as "refresh the session token" and would loop. The message tells the user to fix the key in Settings |
| Rate limited / overloaded / network / upstream | **503 `llm_unavailable`** + `Retry-After: 10` | One handler covers `/query` and the conversation routes |

The anonymous quota is **consumed then refunded** on failure — consume-on-success
would reopen a parallel-request overrun where N concurrent requests each see the
same remaining count. The refund is guarded (`used > 0`) so it can never push a
counter negative. Escalation is best-effort: a failed retry keeps the primary
answer rather than turning a good response into an error.

## Refusing to boot misconfigured

`validate_startup()` runs at **import time**, before the app serves anything:

- `auth_enabled` + a Postgres `EURAG_DATABASE_URL` + **no `EURAG_JWT_SECRET`** →
  **raise**. Each instance would otherwise mint its own random secret, so a token
  minted by one replica is rejected by the next — login "works" but breaks
  intermittently under a load balancer, which is far worse than not booting.
- Missing `EURAG_ENCRYPTION_KEY` → **warn** (BYOK unavailable, chunk text stored
  as plaintext) but boot; this is a valid, if reduced, configuration.

Separately, `EURAG_STRICT_BOOT=true` (prod) turns silent degradation fatal: an
embedder that cannot load its model **raises** rather than falling back to the
hash embedder — same vector dimension, undetectable, and it would quietly poison
a shared Qdrant collection — and a failed seed kills the container instead of
serving 4 sample documents as if they were the corpus.

## Still designed-for
| Control | Status |
|---|---|
| Hash-chained audit (tamper-evident, not just append-only) | future hardening |
| Per-tenant encryption keys | single key today; per-tenant is a KMS swap |
| Prompt-injection test suite | ✅ shipped M6 (prompt framing + tests) |
| Rate limiting / abuse controls | ✅ shipped M6 + access tiers + Turnstile |
| Global spend ceiling | **deliberately absent** — a genuinely hard question should be allowed to escalate. Bound cost via `EURAG_FREE_ANON_QUESTIONS` instead |
| Account recovery | accounts have no email → no password reset |
| Monitoring / alerting | none; no error tracking or spend alarm |

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
| 9 | Secrets in the repo | `.env*` gitignored (with `.env.example` / `.env.local.example` negated); config reads env only; JWT secret + encryption key are env-provided | ✅ active |
| 10 | Forged `X-Forwarded-For` mints unlimited free questions | `EURAG_TRUST_PROXY` defaults off; quota and limiter share one guarded helper; verified against the live stack that a forged header does not create a new bucket | ✅ enforced |
| 11 | Bots drain the owner's API credits via the anon tier | Turnstile before `consume` on `/query` and on `/auth/register`; per-IP/day server-side quota; Redis rate limiting. Residual: a human rotating IPs | ✅ enforced (bounded, not eliminated) |
| 12 | Multi-instance login breaks on per-instance JWT secrets | `validate_startup` refuses to boot on auth + Postgres without `EURAG_JWT_SECRET` | ✅ enforced |
| 13 | Silent corpus/embedder degradation serves wrong answers | `EURAG_STRICT_BOOT` makes embedder fallback and seed failure fatal; `--expect-docs 47` fails a short deploy | ✅ enforced |
| 14 | LLM outage leaks a stack trace / steals a free question | Typed `LLMUnavailableError` → 400/503 with a friendly message; anon quota refunded on failure | ✅ enforced |

**Deployment note:** with `EURAG_AUTH_ENABLED=true`, `EURAG_JWT_SECRET` set,
and `EURAG_ENCRYPTION_KEY` set, EURAG is safe to run multi-user. Left off, it
is a local single-user tool.

**Before a public URL**, check the two that are configuration rather than code:
real Turnstile keys (the universal test keys pass everything) and a considered
`EURAG_FREE_ANON_QUESTIONS` — the anonymous tier spends the *server's* Anthropic
key on full-quality, escalation-enabled answers.
