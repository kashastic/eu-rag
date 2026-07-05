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
    def __init__(self, model_name: str):
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self._model = TextCrossEncoder(model_name=model_name)
        self.name = f"fastembed-cross-encoder:{model_name}"

    def rank(self, query: str, texts: list[str]) -> list[int]:
        scores = list(self._model.rerank(query, texts))
        return sorted(range(len(texts)), key=lambda i: scores[i], reverse=True)


def get_reranker(spec: str) -> Reranker | None:
    """spec: "none"/empty disables; otherwise a fastembed cross-encoder model
    name (e.g. "Xenova/ms-marco-MiniLM-L-6-v2"). Falls back to no reranking
    if the model can't be loaded — retrieval must not hard-fail offline."""
    if not spec or spec == "none":
        return None
    try:
        return CrossEncoderReranker(spec)
    except Exception as exc:
        logger.warning("reranker %s unavailable (%s) — continuing without", spec, exc)
        return None
