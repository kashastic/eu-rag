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
    # cross-encoder reranker: "none" disables; otherwise a fastembed
    # TextCrossEncoder model name. Default measured on the golden harness
    # (DEVLOG 2026-07-05): phrase_hit 82%→88% at doc_hit 100%, costing ~1s
    # per query on CPU — acceptable next to LLM generation time.
    reranker: str = field(
        default_factory=lambda: os.environ.get(
            "EURAG_RERANKER", "Xenova/ms-marco-MiniLM-L-6-v2"
        )
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
    # Rate limit on /query and /ingest, per client (user or IP). 0 disables.
    rate_limit_per_min: int = field(
        default_factory=lambda: int(os.environ.get("EURAG_RATE_LIMIT_PER_MIN", "30"))
    )
    rate_limit_burst: int = field(
        default_factory=lambda: int(os.environ.get("EURAG_RATE_LIMIT_BURST", "10"))
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
