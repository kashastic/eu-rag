"""Cross-encoder reranking: score query/passage pairs jointly to reorder the
fused candidate pool. Bi-encoder + BM25 recall is good at finding the right
document; the cross-encoder is what promotes the passage that actually
answers the question (see harness phrase_hit metric).
"""

import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class Reranker(Protocol):
    name: str

    def rank(self, query: str, texts: list[str]) -> list[int]:
        """Indices into texts, best first."""
        ...


class CrossEncoderReranker:
    """batch_size caps how many query/passage pairs are scored in one forward
    pass. fastembed's own default (64) exceeds every pool this retriever
    builds, so the entire pool went through at once and peak memory tracked
    pool size — 60 pairs on the escalation path vs 30 on the normal one.
    Pairs are scored independently, so a smaller batch yields the same
    scores; it only trades a little latency for a much lower peak."""

    def __init__(self, model_name: str, batch_size: int = 8):
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self._model = TextCrossEncoder(model_name=model_name)
        self._batch_size = batch_size
        self.name = f"fastembed-cross-encoder:{model_name}"

    def rank(self, query: str, texts: list[str]) -> list[int]:
        scores = list(
            self._model.rerank(query, texts, batch_size=self._batch_size)
        )
        return sorted(range(len(texts)), key=lambda i: scores[i], reverse=True)


def get_reranker(spec: str, batch_size: int = 8) -> Reranker | None:
    """spec: "none"/empty disables; otherwise a fastembed cross-encoder model
    name (e.g. "Xenova/ms-marco-MiniLM-L-6-v2"). Falls back to no reranking
    if the model can't be loaded — retrieval must not hard-fail offline."""
    if not spec or spec == "none":
        return None
    try:
        return CrossEncoderReranker(spec, batch_size)
    except Exception as exc:
        logger.warning("reranker %s unavailable (%s) — continuing without", spec, exc)
        return None
