# Deploying EURAG (multi-instance, live)

Three ways to run EURAG, smallest to largest.

## 1. Local, single user (no login)
```bash
python -m data.seed && uvicorn api.main:app   # → http://localhost:8000
```
The bundled static chat UI, no accounts. Auth off, SQLite, embedded Qdrant.

## 2. Single container
```bash
docker compose up   # → http://localhost:8000
```
See [`docker-compose.yml`](../docker-compose.yml). Still single-instance.

## 3. Production, horizontally scalable
```bash
cp .env.example .env   # fill POSTGRES_PASSWORD, EURAG_JWT_SECRET,
                       # EURAG_ENCRYPTION_KEY, ANTHROPIC_API_KEY
docker compose -f docker-compose.prod.yml up --build
```
[`docker-compose.prod.yml`](../docker-compose.prod.yml) brings up Postgres,
Qdrant, Redis, **two** API replicas, the Next.js web app, and Caddy as a
single-origin reverse proxy (auto-HTTPS with a real domain via `EURAG_DOMAIN`).

### Why it scales

Every piece of mutable state is shared, so the `api` service can run N
replicas behind Caddy with no stickiness:

| State | Shared via | Instance-safe? |
|---|---|---|
| Users, refresh tokens, audit | **Postgres** (`EURAG_DATABASE_URL`) | ✅ login/refresh/audit consistent fleet-wide; refresh tokens are single-use across all instances |
| Saved chats (conversations, messages) | **Postgres** | ✅ history identical on every instance |
| Vectors | **Qdrant server** (`QDRANT_URL`) | ✅ |
| Rate-limit buckets | **Redis** (`EURAG_REDIS_URL`) | ✅ one client's limit shared across instances |
| JWT validation | stateless (shared `EURAG_JWT_SECRET`) | ✅ any instance validates any token |

Access tokens are stateless HS256 — as long as every instance shares
`EURAG_JWT_SECRET`, a token minted on one is accepted by all.

### One documented boundary: the corpus registry

The **official 47-document corpus** is read-only and seeded deterministically,
so each replica holds an identical copy — reads are correct across instances.
**User uploads** (`POST /ingest`), a secondary feature, currently write chunk
text to the receiving instance's local registry (vectors do go to shared
Qdrant). So an uploaded document is fully searchable only on the instance that
received it until the registry is also moved to Postgres. The `core/db.py`
layer already supports this; porting `core/registry.py` onto it (the same swap
done for auth and conversations) closes the gap. Until then, either keep
uploads on a single instance or treat the official corpus as the shared source.

### Access tiers & API-cost protection

Anonymous visitors get `EURAG_FREE_ANON_QUESTIONS` (default 3) full-quality
questions, enforced **server-side per IP/day**; then a login wall. Logged-in
users are on Haiku (free) unless they add their own Anthropic key (BYOK, full
cascade on their bill). See `docs/SECURITY.md` for the model. Two deploy TODOs
to harden the anonymous surface:
- **Cloudflare Turnstile** at the anonymous boundary defeats IP-rotation abuse
  of the free questions — add your Turnstile site/secret keys (a seam is left
  in the query path; wire the token check there).
- **Google login** — the "Continue with Google" button is present but disabled;
  provide a Google OAuth client ID/secret and an `/auth/google` callback to
  enable it.

### Operational notes
- **Secrets**: `EURAG_JWT_SECRET` and `EURAG_ENCRYPTION_KEY` — `openssl rand -hex 32` each. Rotating the encryption key requires a re-seed (version-prefixed ciphertext lets old rows still read).
- **Corpus**: mount a populated `data/raw/` (run the scrapers once) so the first-boot seed loads all 47 documents; otherwise it seeds the samples.
- **Scaling the API**: `docker compose -f docker-compose.prod.yml up --scale api=4`.
- **Postgres parity** is tested — `EURAG_TEST_DATABASE_URL=… pytest tests/test_postgres.py`.
