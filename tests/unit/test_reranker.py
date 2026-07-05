"""Reranker plumbing: ordering is respected, offline fallback never fails."""

from core.ingestion.chunker import Chunk
from core.retrieval.bm25 import BM25Index
from core.retrieval.hybrid_retriever import HybridRetriever
from core.retrieval.reranker import get_reranker


class _NoVectors:
    def search(self, vector, k):
        return []


class _NullEmbedder:
    def embed_query(self, query):
        return [0.0]


class _ReverseReranker:
    """Deterministic fake: prefers the candidate fusion ranked last."""

    name = "reverse"

    def rank(self, query, texts):
        return list(reversed(range(len(texts))))


def _chunk(chunk_id: str, text: str) -> Chunk:
    doc_id, index = chunk_id.rsplit(":", 1)
    return Chunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        index=int(index),
        text=text,
        title=doc_id,
        source_url="",
    )


def _retriever(chunks: dict[str, str], reranker) -> HybridRetriever:
    bm25 = BM25Index()
    for chunk_id, text in chunks.items():
        bm25.add(chunk_id, text)
    return HybridRetriever(
        bm25,
        _NoVectors(),
        _NullEmbedder(),
        reranker=reranker,
        get_chunks=lambda ids: [_chunk(cid, chunks[cid]) for cid in ids],
    )


def test_reranker_reorders_fused_pool():
    chunks = {f"doc{i}:0": f"widget rules {'relevant ' * (5 - i)}" for i in range(4)}
    baseline = _retriever(chunks, reranker=None).retrieve("widget rules relevant", k=4)
    reranked = _retriever(chunks, _ReverseReranker()).retrieve(
        "widget rules relevant", k=4
    )
    assert reranked == list(reversed(baseline))


def test_per_doc_cap_applies_after_reranking():
    chunks = {f"docA:{i}": "widget rules " * (6 - i) for i in range(5)}
    chunks["docB:0"] = "widget rules for gatekeepers"
    retriever = _retriever(chunks, _ReverseReranker())
    ids = retriever.retrieve("widget rules", k=3)
    assert sum(cid.startswith("docA:") for cid in ids) <= 2
    assert "docB:0" in ids


def test_get_reranker_none_and_unavailable_model():
    assert get_reranker("none") is None
    assert get_reranker("") is None
    # unknown model must degrade to no reranking, never raise
    assert get_reranker("no-such/model-anywhere") is None
