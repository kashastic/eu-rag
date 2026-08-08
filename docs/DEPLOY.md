# Deploying EURAG (multi-instance, live)

Three ways to run EURAG, smallest to largest, then **§4 sizing**, the
**§5 free-tier deploy runbook**, and **§6 host options** once the credit ends.

## 1. Local, single user (no login)
```bash
python -m data.seed && uvicorn api.main:app   # → http://localhost:8000
```
The bundled static chat UI, no accounts. Auth off, SQLite, embedded Qdrant.
Zero configuration — every production flag defaults to off.

## 2. Single container
```bash
docker compose up   # → http://localhost:8000
```
See [`docker-compose.yml`](../docker-compose.yml). Still single-instance.

## 3. Production, horizontally scalable

```bash
cp .env.example .env   # fill POSTGRES_PASSWORD, EURAG_JWT_SECRET,
                       # EURAG_ENCRYPTION_KEY, ANTHROPIC_API_KEY
docker compose -f docker-compose.prod.yml up --build -d
```

[`docker-compose.prod.yml`](../docker-compose.prod.yml) brings up Postgres,
Qdrant, Redis, a one-shot **seeder**, **two** API replicas, the Next.js web app,
and Caddy as a single-origin reverse proxy (auto-HTTPS with a real domain via
`EURAG_DOMAIN`).

> **Rehearsing locally?** Put the secrets in `.env.prod.local` and pass
> `--env-file .env.prod.local`, **not** in `.env`. `pydantic-settings` reads
> `.env`, so an `EURAG_TURNSTILE_SECRET` there switches the bot gate on inside
> the test suite and 13 tests fail with `KeyError: 'access_token'`. CI has no
> `.env` and stays green, so this only bites locally. On a server `.env` is fine.

### Boot order

The `seeder` service fetches any missing `data/raw` caches, embeds, and writes
the registry + vectors, then exits. API replicas wait on
`service_completed_successfully`, so **they never scrape, seed, or download
models** — they mount the same `apivar` and `modelcache` volumes and just serve.
`EURAG_EXPECT_DOCS` (default 47) makes a short corpus a hard failure instead of
a quietly 4-document deploy.

Measured on a populated `data/raw`: cold seed **~3m20s** (4202 chunks), replicas
healthy **~12s** later. A re-run is **~4s** — all 47 documents hash-skip and
nothing re-embeds, so `git pull && up -d` is cheap. With an empty `data/raw`,
add ~6 min of scraping (EUR-Lex enforces a 10s crawl delay).

### Why it scales

Every piece of mutable state is shared, so the `api` service can run N
replicas behind Caddy with no stickiness:

| State | Shared via | Instance-safe? |
|---|---|---|
| Users, refresh tokens, audit | **Postgres** (`EURAG_DATABASE_URL`) | ✅ login/refresh/audit consistent fleet-wide; refresh tokens are single-use across all instances |
| Saved chats (conversations, messages) | **Postgres** | ✅ history identical on every instance |
| Vectors | **Qdrant server** (`QDRANT_URL`) | ✅ |
| Rate-limit buckets | **Redis** (`EURAG_REDIS_URL`) | ✅ one client's limit shared across instances |
| Anonymous quota | **Postgres** (`anon_quota`) | ✅ per-IP budget holds fleet-wide |
| Official corpus | `apivar` volume, seeded once | ✅ read-only, identical everywhere |
| JWT validation | stateless (shared `EURAG_JWT_SECRET`) | ✅ any instance validates any token |

Access tokens are stateless HS256 — as long as every instance shares
`EURAG_JWT_SECRET`, a token minted on one is accepted by all. Verified by
killing a replica mid-session: Caddy's `api` upstream drops the dead container
from DNS and the survivor keeps answering.

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

### Fail-loud production boot

`EURAG_STRICT_BOOT=true` (set in the prod compose) turns silent degradation into
a crash:
- an embedder that can't load its model **raises** instead of falling back to
  the hash embedder — same vector dimension, undetectable, and it would poison
  shared Qdrant;
- a failed seed kills the container instead of serving 4 sample documents.

Independently, `validate_startup` refuses to boot when `auth_enabled` and a
Postgres `EURAG_DATABASE_URL` are set without `EURAG_JWT_SECRET` (per-instance
auto-secrets silently break multi-instance login), and warns when
`EURAG_ENCRYPTION_KEY` is missing.

### Access tiers, bot gate, and client IPs

Anonymous visitors get `EURAG_FREE_ANON_QUESTIONS` (default 3) full-quality
questions, enforced **server-side per IP/day**; then a login wall. Logged-in
users are on Haiku (free) unless they add their own Anthropic key (BYOK, full
cascade on their bill). See [`SECURITY.md`](SECURITY.md) for the threat model.

- **Cloudflare Turnstile** gates anonymous questions and registration whenever
  `EURAG_TURNSTILE_SECRET` is set (unset = off, so local mode is untouched). The
  sitekey is served at runtime from `/healthz`, so rotating keys is an env change
  with **no rebuild**. For testing use Cloudflare's universal keys: sitekey
  `1x00000000000000000000AA` with secret `1x0000000000000000000000000000000AA`
  (always passes), or secret `2x0000000000000000000000000000000AA` to force
  rejection.
- **`EURAG_TRUST_PROXY=true`** (set in the prod compose) makes the anon-quota and
  rate-limit key the first `X-Forwarded-For` hop. Set it **only** where nothing
  but a header-rewriting proxy can reach the API — on a directly reachable API a
  forged header mints unlimited free questions. Verified against this Caddy: a
  forged `X-Forwarded-For` does **not** create a new quota bucket.
- **If you put Cloudflare's proxy (orange cloud) in front of Caddy**, the
  immediate peer becomes a Cloudflare edge IP and *every* visitor collapses into
  a handful of quota/rate-limit buckets. Either configure Caddy's
  `trusted_proxies` with Cloudflare's ranges (or key off `CF-Connecting-IP`), or
  run Cloudflare **DNS-only** and let Caddy terminate TLS. Turnstile does not
  require Cloudflare to be in the traffic path.
- **Google login** — the "Continue with Google" button is present but disabled;
  provide a Google OAuth client ID/secret and an `/auth/google` callback.

### LLM failure handling

`LLMUnavailableError` maps to friendly statuses instead of raw 500s: a rejected
BYOK key → **400 `byok_key_rejected`**, everything else (rate limit, overload,
network) → **503 `llm_unavailable` + `Retry-After: 10`**. An anonymous question
consumed before a failed call is **refunded**, and escalation is best-effort — a
failed retry keeps the primary answer.

## 4. Sizing: what this stack actually needs

Measured on the running prod stack (2026-08-08), `docker stats`:

| Container | Idle | Peak, serving one query |
|---|---|---|
| `api` (each replica) | 835 MB | **1.83 GB** |
| `qdrant` | 302 MB | 304 MB |
| `web` | 94 MB | 94 MB |
| `caddy` + `postgres` + `redis` | 131 MB | 131 MB |
| **Total, 2 replicas** | **2.2 GB** | **3.2 GB** |
| **Total, 1 replica** | ~1.4 GB | **~2.4 GB** |

An API replica **more than doubles** while answering — the reranker and embedding
work are transient. Size for peak, not idle.

**Consequence: every 1 GB "always free"/12-month VM is unusable** — AWS
`t3.micro`, Azure `B1S`, GCP `e2-micro` all OOM on the first question. The floor
is **4 GB** for a comfortable single-replica deployment.

Cheapest lever if memory is tight: set `replicas: 1` in the prod compose
(−835 MB). Do *not* reach for `EURAG_RERANKER=none` — it is worth ~6pp of
phrase-hit accuracy, and it is a retrieval change, so it needs before/after
harness numbers per the standing rule.

## 5. Free-tier runbook (GCP trial credit + a free subdomain)

**Hosting is covered by credit; the Anthropic API is not** — see "Access tiers"
above and decide `EURAG_FREE_ANON_QUESTIONS` before going public.

**Why this target:** a new GCP account gets **$300 of credit over 90 days**. An
`e2-medium` (2 vCPU, 4 GB) runs ~$25–30/mo, so the full 90 days costs roughly
$90 of the $300 and fits the peak measured above. GCP's always-free `e2-micro`
is 1 GB — ignore it, the credit is the point.

> **Note the architecture switch.** GCP `e2` is **x86_64**, while the images
> verified locally are `linux/arm64` (built on Apple Silicon). Do **not** push
> images from your laptop — the runbook builds on the VM, which produces amd64
> natively. Lower risk, not higher: amd64 is the best-supported target for
> `onnxruntime`/`fastembed`.

### 5.1 Create the VM
1. GCP Console → Compute Engine → **Create instance**. (Enable the Compute
   Engine API first if prompted; billing must be on, with the trial credit
   applied — the trial does not auto-charge when it ends, it pauses resources.)
2. Machine type **`e2-medium`** (2 vCPU, 4 GB). Region: pick one near your users.
3. Boot disk → **Ubuntu 24.04 LTS**, size **30 GB** (the default 10 GB is too
   small: the API image alone is ~1.8 GB, plus ~200 MB of ONNX models and the
   corpus).
4. Firewall → tick **Allow HTTP traffic** and **Allow HTTPS traffic**. That is
   the whole firewall step — unlike Oracle, GCP's Ubuntu images do **not** ship
   host iptables rules that block 80/443.
5. After creation, reserve a **static external IP** (VPC network → IP addresses →
   promote the ephemeral one). Otherwise the IP changes on stop/start and breaks
   your DNS record.

### 5.2 Install Docker
```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker $USER && newgrp docker
```

### 5.3 Point a free subdomain at it
Register a subdomain (e.g. DuckDNS) and set its A record to the instance's public
IP. Caddy issues a real Let's Encrypt certificate over the HTTP-01 challenge, so
no DNS API token is needed — just reachable port 80. Verify `dig +short
<your-subdomain>` returns the VM's IP **before** bringing the stack up, or the
first cert attempt fails and Caddy backs off.

### 5.4 Deploy
```bash
git clone <your-repo> eurag && cd eurag
cat > .env <<'EOF'
POSTGRES_PASSWORD=<openssl rand -hex 16>
EURAG_JWT_SECRET=<openssl rand -hex 32>
EURAG_ENCRYPTION_KEY=<openssl rand -hex 32>
ANTHROPIC_API_KEY=<your key>
EURAG_TURNSTILE_SITEKEY=<from Cloudflare, or the test sitekey>
EURAG_TURNSTILE_SECRET=<from Cloudflare, or the test secret>
EURAG_DOMAIN=<your-subdomain>
EOF
docker compose -f docker-compose.prod.yml up --build -d
docker compose -f docker-compose.prod.yml logs -f seeder   # want: "Seeded 47 documents", exit 0
```
`data/raw/` is gitignored, so a fresh clone has no corpus cache and the seeder
scrapes it (~6 min) before embedding (~3m20s). To skip the scrape, copy a
populated cache up first: `rsync -a data/raw/ <vm>:~/eurag/data/raw/`.

Building the images on 2 vCPU takes a while; it is a one-time cost.

### 5.5 Smoke test
```bash
curl -s https://<your-subdomain>/healthz | python3 -m json.tool
# want: documents=47, embedder=fastembed:…, auth_enabled=true,
#       encryption=true, turnstile_sitekey set
```
Then in a browser: anon question → cited answer → exhaust the free questions →
login wall → register → free-tier banner → add a BYOK key → `tier: byok`.
A bad BYOK key must produce a friendly 400, not a 500.

### 5.6 Keep it alive
- `restart: unless-stopped` on every service covers reboots.
- Updates: `git pull && docker compose -f docker-compose.prod.yml up --build -d`
  — hash-skips mean no re-embed.
- **Set a billing alert** at e.g. $50 and $150 so the credit burn is visible.
- **Watch the 90-day cliff** — see §6 before the credit runs out.

## 6. When the credit expires (and hosts that were ruled out)

The GCP credit buys 90 days, not a home. Landing options, best first:

- **Hetzner `CAX11`** (2 vCPU / 4 GB ARM, ~€4/mo) — not free, but the cheapest
  thing that comfortably fits the sizing above, and ARM64 is already proven for
  these images. The realistic long-term answer.
- **Oracle Always Free** (`VM.Standard.A1.Flex`, up to 4 OCPU / 24 GB, free
  indefinitely) — the only free-forever option that fits. Ruled out for us in
  Aug 2026: account signup itself was rejected. If support ever unblocks it,
  note that A1 capacity is also frequently exhausted ("Out of host capacity"),
  that upgrading to Pay As You Go greatly improves availability while keeping
  Always Free resources free, and that Oracle's Ubuntu images ship host iptables
  rules dropping 80/443 — that is the classic "Caddy can't get a cert" cause:
  ```bash
  sudo iptables -L INPUT --line-numbers -n   # find the catch-all REJECT line
  sudo iptables -I INPUT <that number> -p tcp --dport 80  -j ACCEPT
  sudo iptables -I INPUT <that number> -p tcp --dport 443 -j ACCEPT
  sudo netfilter-persistent save
  ```
- **Ruled out — 1 GB RAM**: AWS `t3.micro`, Azure `B1S`, GCP `e2-micro`. Per §4
  the stack peaks at ~2.4 GB on one replica; these cannot run it. AWS's newer
  accounts are credit-based rather than the old 12-month allowance, but the
  hardware on offer is the same 1 GB box either way.
- **Ruled out — 512 MB and/or sleeps**: Render free web services, most
  "free tier" PaaS. Below the floor.

## Operational notes
- **Secrets**: `EURAG_JWT_SECRET` and `EURAG_ENCRYPTION_KEY` — `openssl rand -hex 32` each. Rotating the encryption key requires a re-seed (version-prefixed ciphertext lets old rows still read). BYOK keys are stored `enc1:`-prefixed AES-256-GCM ciphertext, never plaintext.
- **Backups**: `docker compose -f docker-compose.prod.yml exec -T postgres pg_dump -U eurag eurag | gzip > eurag-$(date +%F).sql.gz` on a daily cron, copied off-host. Volumes worth keeping: `pgdata` (accounts, chats, audit, quota), `qdrant`, `apivar`. `data/raw` is re-fetchable, and `modelcache` is re-downloadable.
- **Scaling the API**: `docker compose -f docker-compose.prod.yml up -d --scale api=4`. `data.seed` takes an `flock` on `data/raw/.seed.lock` as insurance.
- **Postgres parity** is tested — `EURAG_TEST_DATABASE_URL=… pytest tests/test_postgres.py`.
- **Rate limits**: `EURAG_RATE_LIMIT_PER_MIN` / `_BURST` are read at import time — changing them needs a process restart, not just a new env value.
