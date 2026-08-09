"""Reranker plumbing: ordering is respected, offline fallback never fails."""

from core.ingestion.chunker import Chunk
from core.retrieval.bm25 import BM25Index
from core.retrieval.hybrid_retriever import HybridRetriever
from core.retrieval.reranker import CrossEncoderReranker, get_reranker


class _NoVectors:
    def search(self, vector, k, tenants=None):
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
        get_chunks=lambda ids, tenants=None: [_chunk(cid, chunks[cid]) for cid in ids],
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


class _RecordingCrossEncoder:
    """Stands in for fastembed's TextCrossEncoder so no model is downloaded."""

    last_batch_size: int | None = None

    def __init__(self, model_name):
        self.model_name = model_name

    def rerank(self, query, documents, batch_size=64, **kwargs):
        _RecordingCrossEncoder.last_batch_size = batch_size
        return [float(i) for i in range(len(list(documents)))]


def _patch_cross_encoder(monkeypatch):
    import fastembed.rerank.cross_encoder as ce

    _RecordingCrossEncoder.last_batch_size = None
    monkeypatch.setattr(ce, "TextCrossEncoder", _RecordingCrossEncoder)


def test_batch_size_is_forwarded_to_the_model(monkeypatch):
    """Batch size is the memory ceiling on the escalation path, not a tuning
    nicety: fastembed defaults to 64, above every pool the retriever builds,
    so a 60-candidate pool went through in one forward pass and allocated
    1.6GB — enough to get the api container OOM-killed on a 4GB host
    (DEVLOG 2026-08-09). If this argument stops being forwarded, peak memory
    regresses ~5.7x with no other visible symptom, so assert it explicitly."""
    _patch_cross_encoder(monkeypatch)

    CrossEncoderReranker("any/model", batch_size=8).rank("q", ["a", "b", "c"])

    assert _RecordingCrossEncoder.last_batch_size == 8


def test_get_reranker_forwards_batch_size(monkeypatch):
    _patch_cross_encoder(monkeypatch)

    get_reranker("any/model", batch_size=16).rank("q", ["a", "b"])

    assert _RecordingCrossEncoder.last_batch_size == 16


def test_rank_is_order_preserving_regardless_of_batch_size(monkeypatch):
    """Scores are per-pair independent, so batching changes peak memory and
    nothing else. Verified end to end on the golden harness: every metric is
    identical from batch=4 to batch=64 with HyDE disabled (DEVLOG
    2026-08-09)."""
    _patch_cross_encoder(monkeypatch)
    texts = ["a", "b", "c", "d"]

    small = CrossEncoderReranker("any/model", batch_size=2).rank("q", texts)
    large = CrossEncoderReranker("any/model", batch_size=64).rank("q", texts)

    assert small == large
