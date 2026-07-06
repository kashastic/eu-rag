from core.retrieval.bm25 import BM25Index
from core.retrieval.hybrid_retriever import HybridRetriever, rrf_fuse


class _NoVectors:
    def search(self, vector, k, tenants=None):
        return []


class _NullEmbedder:
    def embed_query(self, query):
        return [0.0]


def _lexical_retriever(chunks: dict[str, str]) -> HybridRetriever:
    bm25 = BM25Index()
    for chunk_id, text in chunks.items():
        bm25.add(chunk_id, text)
    return HybridRetriever(bm25, _NoVectors(), _NullEmbedder())


def test_retrieve_caps_chunks_per_document():
    chunks = {f"docA:{i}": "widget safety obligations " * (9 - i) for i in range(8)}
    chunks["docB:0"] = "widget safety obligations for market gatekeepers"
    chunks["docC:0"] = "widget safety obligations in the internal market"
    retriever = _lexical_retriever(chunks)
    ids = retriever.retrieve("widget safety obligations", k=4)
    assert len(ids) == 4
    assert sum(cid.startswith("docA:") for cid in ids) <= 2
    assert "docB:0" in ids and "docC:0" in ids


def test_retrieve_backfills_past_cap_for_tiny_corpus():
    chunks = {f"docA:{i}": f"widget rules part {i}" for i in range(5)}
    retriever = _lexical_retriever(chunks)
    assert len(retriever.retrieve("widget rules", k=4)) == 4


def test_item_in_both_rankings_beats_single_ranking_items():
    fused = rrf_fuse([["a", "b", "c"], ["b", "d"]])
    assert fused[0][0] == "b"


def test_order_within_ranking_matters():
    fused = rrf_fuse([["a", "b"]])
    assert fused[0][0] == "a"
    assert fused[0][1] > fused[1][1]


def test_empty_rankings():
    assert rrf_fuse([]) == []
    assert rrf_fuse([[], []]) == []
