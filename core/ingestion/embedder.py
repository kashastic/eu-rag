"""Embedding abstraction: FastEmbed (multilingual ONNX) with an offline
hashing fallback so tests and cold-start dev never need a model download.
"""

import hashlib
import logging
import math
import re
from typing import Protocol

logger = logging.getLogger(__name__)


class Embedder(Protocol):
    name: str
    dim: int

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


_TOKEN_RE = re.compile(r"[a-zà-öø-ÿ0-9]+", re.IGNORECASE)


class HashingEmbedder:
    """Deterministic bag-of-hashed-tokens embedding. Not semantically smart —
    BM25 carries retrieval quality when this fallback is active."""

    name = "hash"
    dim = 384

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in _TOKEN_RE.findall(text.lower()):
            h = int.from_bytes(
                hashlib.blake2b(token.encode(), digest_size=8).digest(), "big"
            )
            vec[h % self.dim] += 1.0 if (h >> 63) else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class FastEmbedEmbedder:
    def __init__(self, model_name: str):
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=model_name)
        self.name = f"fastembed:{model_name}"
        self.dim = len(next(iter(self._model.embed(["dim probe"]))))

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]


def get_embedder(kind: str, model_name: str) -> Embedder:
    if kind == "hash":
        return HashingEmbedder()
    try:
        return FastEmbedEmbedder(model_name)
    except Exception as exc:  # model download failed / package missing
        logger.warning(
            "FastEmbed unavailable (%s) — falling back to hashing embedder; "
            "retrieval quality relies on BM25 until a real embedder is configured",
            exc,
        )
        return HashingEmbedder()
