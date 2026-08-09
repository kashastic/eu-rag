"""Central runtime configuration, sourced from environment variables."""

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env loader (no dependency): KEY=VALUE lines, # comments.
    Real environment variables always win over .env values."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv()


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("EURAG_DATA_DIR", "var"))
    )
    # "fastembed" (real multilingual embeddings, downloads a model on first use)
    # or "hash" (deterministic, offline — tests and cold-start dev)
    embedder: str = field(
        default_factory=lambda: os.environ.get("EURAG_EMBEDDER", "fastembed")
    )
    embed_model: str = field(
        default_factory=lambda: os.environ.get(
            "EURAG_EMBED_MODEL",
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        )
    )
    # Answer generation is grounded in retrieved passages with enforced
    # citations, so Sonnet-tier quality suffices; Opus was 5/25 $/MTok,
    # Sonnet 5 is 3/15 (2/10 intro pricing through 2026-08-31).
    llm_model: str = field(
        default_factory=lambda: os.environ.get("EURAG_LLM_MODEL", "claude-sonnet-5")
    )
    # When the primary answer is low-confidence (model signals insufficient
    # sources, or citation validation failed), answer once more with this
    # model over wider retrieval. "none" disables the cascade.
    escalation_model: str = field(
        default_factory=lambda: os.environ.get(
            "EURAG_ESCALATION_MODEL", "claude-opus-4-8"
        )
    )
    escalation_top_k: int = field(
        default_factory=lambda: int(os.environ.get("EURAG_ESCALATION_TOP_K", "12"))
    )
    # query-time expansion, both via a small cheap model; "none" disables.
    # Defaults set by golden-harness measurement (DEVLOG 2026-07-06): HyDE
    # lifted compound-question retrieval 67%→100% at one Haiku call per
    # query; decomposition showed no gain on top of HyDE, so it ships off.
    hyde_model: str = field(
        default_factory=lambda: os.environ.get("EURAG_HYDE_MODEL", "claude-haiku-4-5")
    )
    decompose_model: str = field(
        default_factory=lambda: os.environ.get("EURAG_DECOMPOSE_MODEL", "none")
    )
    # Follow-up contextualisation: rewrites "what if I have 29 people?" into a
    # standalone question using prior turns, BEFORE retrieval. Costs one Haiku
    # call, and only on questions that actually carry history — a first
    # question is untouched. "none" disables (and local single-user mode never
    # sends history, so it is inert there either way).
    contextualize_model: str = field(
        default_factory=lambda: os.environ.get(
            "EURAG_CONTEXTUALIZE_MODEL", "claude-haiku-4-5"
        )
    )
    # cross-encoder reranker: "none" disables; otherwise a fastembed
    # TextCrossEncoder model name. Default measured on the golden harness
    # (DEVLOG 2026-07-05): phrase_hit 82%→88% at doc_hit 100%, costing ~1s
    # per query on CPU — acceptable next to LLM generation time.
    reranker: str = field(
        default_factory=lambda: os.environ.get(
            "EURAG_RERANKER", "Xenova/ms-marco-MiniLM-L-6-v2"
        )
    )
    # Cross-encoder forward-pass batch size. fastembed defaults to 64, which
    # is above every pool this retriever builds (hybrid_retriever: k*5, so 30
    # at top_k=6 but 60 on the escalation path at top_k=12) — so the whole
    # pool went through in ONE pass and the escalation path allocated double
    # the activations of a normal query. That spike OOM-killed the api
    # container on a 4GB host in production (DEVLOG 2026-08-09). Scores are
    # per-pair independent, so this bounds peak memory without changing which
    # chunks win; harness metrics are unmoved at 8 vs 64.
    rerank_batch: int = field(
        default_factory=lambda: int(os.environ.get("EURAG_RERANK_BATCH", "8"))
    )
    # Qdrant: embedded local mode by default; set QDRANT_URL for a server
    qdrant_url: str | None = field(
        default_factory=lambda: os.environ.get("QDRANT_URL") or None
    )
    top_k: int = field(default_factory=lambda: int(os.environ.get("EURAG_TOP_K", "6")))

    # --- M3 security spine ---------------------------------------------------
    # Auth off by default keeps the local single-user story: no tokens, every
    # request runs as a built-in admin over the public corpus. Turn on to
    # require JWTs and isolate per-user uploads into private tenants.
    auth_enabled: bool = field(
        default_factory=lambda: os.environ.get("EURAG_AUTH_ENABLED", "").lower()
        in ("1", "true", "yes")
    )
    jwt_secret: str | None = field(
        default_factory=lambda: os.environ.get("EURAG_JWT_SECRET") or None
    )
    # 64 hex chars (32 bytes) enables AES-256-GCM at-rest encryption of chunk
    # text; unset means plaintext (local default).
    encryption_key: str | None = field(
        default_factory=lambda: os.environ.get("EURAG_ENCRYPTION_KEY") or None
    )
    # PII gate backend for uploads: "regex" (stdlib, default) or "presidio".
    pii_backend: str = field(
        default_factory=lambda: os.environ.get("EURAG_PII_BACKEND", "regex")
    )
    # Fail loud instead of degrading: when true, an embedder that can't load
    # its model raises instead of silently falling back to hashing (which
    # would write low-quality vectors into a shared Qdrant), and the Docker
    # entrypoint treats a failed seed as fatal. Prod sets this; local dev and
    # tests keep the lenient default.
    strict_boot: bool = field(
        default_factory=lambda: os.environ.get("EURAG_STRICT_BOOT", "").lower()
        in ("1", "true", "yes")
    )
    # --- access tiers (cost control) ---
    # Anonymous users get this many full-quality questions (the Sonnet→Opus
    # cascade), counted server-side per IP/day, before a login wall.
    free_anon_questions: int = field(
        default_factory=lambda: int(os.environ.get("EURAG_FREE_ANON_QUESTIONS", "3"))
    )
    # Logged-in free tier answers with this cheap model and no escalation.
    # BYOK users get the full cascade on their own key instead.
    free_model: str = field(
        default_factory=lambda: os.environ.get("EURAG_FREE_MODEL", "claude-haiku-4-5")
    )
    # Cloudflare Turnstile at the anonymous boundary (bot protection for the
    # free-question funnel and registration). Secret unset = check off (local
    # default); sitekey unset = the web app renders no widget. Served to the
    # frontend at runtime via /healthz, so rotating keys is an env change only.
    turnstile_secret: str | None = field(
        default_factory=lambda: os.environ.get("EURAG_TURNSTILE_SECRET") or None
    )
    turnstile_sitekey: str | None = field(
        default_factory=lambda: os.environ.get("EURAG_TURNSTILE_SITEKEY") or None
    )
    # Rate limit on /query and /ingest, per client (user or IP). 0 disables.
    rate_limit_per_min: int = field(
        default_factory=lambda: int(os.environ.get("EURAG_RATE_LIMIT_PER_MIN", "30"))
    )
    rate_limit_burst: int = field(
        default_factory=lambda: int(os.environ.get("EURAG_RATE_LIMIT_BURST", "10"))
    )
    # Trust X-Forwarded-For for the per-client identity used by the rate
    # limiter and the anonymous quota. Set this ONLY when the app is reachable
    # exclusively through a reverse proxy that rewrites the header (our Caddy
    # does) — otherwise a client can forge it and mint unlimited buckets.
    # Off = key on the peer address, which behind a proxy puts every visitor
    # in the proxy's single bucket. Prod compose sets it true.
    trust_proxy: bool = field(
        default_factory=lambda: os.environ.get("EURAG_TRUST_PROXY", "").lower()
        in ("1", "true", "yes")
    )
    # Redis URL for a shared rate-limit bucket across instances. Unset = the
    # in-process limiter (correct only for a single instance).
    redis_url: str | None = field(
        default_factory=lambda: os.environ.get("EURAG_REDIS_URL") or None
    )
    # CORS allowed origins (comma-separated) for a split frontend/API deploy.
    # Empty = same-origin only (frontend served behind the same host).
    cors_origins: tuple = field(
        default_factory=lambda: tuple(
            o.strip()
            for o in os.environ.get("EURAG_CORS_ORIGINS", "").split(",")
            if o.strip()
        )
    )

    @property
    def qdrant_path(self) -> Path:
        return self.data_dir / "qdrant"

    @property
    def registry_path(self) -> Path:
        return self.data_dir / "registry.sqlite3"

    @property
    def auth_path(self) -> Path:
        return self.data_dir / "auth.sqlite3"

    @property
    def jwt_secret_path(self) -> Path:
        return self.data_dir / "jwt_secret"


def get_settings() -> Settings:
    return Settings()


def validate_startup(settings: Settings, db_url: str | None) -> None:
    """Refuse to boot a multi-instance deploy with broken shared secrets.

    Auth on + shared Postgres means several replicas must validate each
    other's JWTs; without EURAG_JWT_SECRET each instance mints its own
    secret and logins break silently depending on which replica answers —
    so that raises. A missing encryption key is survivable (BYOK off,
    uploaded chunks at rest in plaintext) and only warns. Local zero-config
    mode (auth off) passes untouched.
    """
    from core.db import is_postgres

    if not settings.auth_enabled:
        return
    if is_postgres(db_url) and settings.jwt_secret is None:
        raise RuntimeError(
            "EURAG_JWT_SECRET must be set to run with auth enabled on a shared "
            "database: per-instance auto-generated secrets break login across "
            "replicas. Generate one with `openssl rand -hex 32`."
        )
    if settings.encryption_key is None:
        import logging

        logging.getLogger(__name__).warning(
            "EURAG_ENCRYPTION_KEY is not set: BYOK key storage is disabled and "
            "uploaded chunk text is stored in plaintext."
        )
