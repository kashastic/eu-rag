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

    @property
    def qdrant_path(self) -> Path:
        return self.data_dir / "qdrant"

    @property
    def registry_path(self) -> Path:
        return self.data_dir / "registry.sqlite3"


def get_settings() -> Settings:
    return Settings()
