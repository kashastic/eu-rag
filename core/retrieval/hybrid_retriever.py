"""Hybrid retrieval: BM25 + vector search fused with Reciprocal Rank Fusion,
optionally reordered by a cross-encoder reranker.

RRF over score normalization because BM25 and cosine scores live on
incomparable scales; rank-based fusion is robust with zero tuning.
"""

from typing import Callable

from core.ingestion.chunker import Chunk
from core.ingestion.embedder import Embedder
from core.retrieval.bm25 import BM25Index
from core.retrieval.reranker import Reranker
from core.retrieval.vector_store import VectorStore

RRF_K = 60


def rrf_fuse(rankings: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


class HybridRetriever:
    def __init__(
        self,
        bm25: BM25Index,
        vectors: VectorStore,
        embedder: Embedder,
        reranker: Reranker | None = None,
        get_chunks: Callable[..., list[Chunk]] | None = None,
        expander=None,  # HydeExpander: rewrites the vector-leg text
        decomposer=None,  # QueryDecomposer: splits compound questions
    ):
        self.bm25 = bm25
        self.vectors = vectors
        self.embedder = embedder
        self.reranker = reranker
        self.get_chunks = get_chunks  # chunk texts for the reranker
        self.expander = expander
        self.decomposer = decomposer

    def retrieve(
        self,
        query: str,
        k: int = 6,
        max_per_doc: int = 2,
        tenants: list[str] | None = None,
    ) -> list[str]:
        """Returns fused chunk_ids, best first, scoped to `tenants`.

        With a reranker, a larger fused pool is reordered by joint
        query/passage scoring before the top-k cut — fusion recall decides
        what is considered, the cross-encoder decides what wins.

        At most max_per_doc chunks per document: full regulations span
        hundreds of chunks each, and without the cap one dominant act
        monopolizes every slot and crowds out the document that actually
        answers the question. Backfills past the cap only when the corpus
        is too small to fill k otherwise."""
        reranking = self.reranker is not None and self.get_chunks is not None
        pool = max(k * 5, 30) if reranking else max(k * 3, 10)

        # compound questions retrieve once per sub-question; the rankings are
        # then fused so each topic contributes candidates
        queries = [query]
        if self.decomposer:
            queries += self.decomposer.subqueries(query)
        rankings = [self._fused(q, pool, tenants) for q in queries]
        fused = (
            [cid for cid, _ in rrf_fuse(rankings)] if len(rankings) > 1 else rankings[0]
        )

        if reranking:
            # get_chunks enforces tenancy: a foreign chunk_id from the global
            # BM25 index is dropped here before it can be reranked or returned
            chunks = self.get_chunks(fused, tenants)
            # rerank against the original question — sub-queries only widen recall
            order = self.reranker.rank(query, [c.text for c in chunks])
            fused = [chunks[i].chunk_id for i in order]

        picked: list[str] = []
        per_doc: dict[str, int] = {}
        for cid in fused:
            doc_id = cid.rsplit(":", 1)[0]
            if per_doc.get(doc_id, 0) >= max_per_doc:
                continue
            picked.append(cid)
            per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
            if len(picked) == k:
                return picked
        for cid in fused:
            if len(picked) == k:
                break
            if cid not in picked:
                picked.append(cid)
        return picked

    def _fused(self, query: str, pool: int, tenants: list[str] | None = None) -> list[str]:
        """BM25 + vector rankings for one query, RRF-fused. HyDE rewrites
        only the vector leg — regulation numbers must stay literal for BM25.

        The vector leg filters by tenant server-side; BM25 is a global
        in-memory index, so its foreign candidates are dropped downstream by
        the tenant-scoped get_chunks — the boundary that actually matters."""
        embed_text = self.expander.expand(query) if self.expander else query
        lexical = [cid for cid, _ in self.bm25.search(query, k=pool)]
        semantic = [
            cid
            for cid, _ in self.vectors.search(
                self.embedder.embed_query(embed_text), k=pool, tenants=tenants
            )
        ]
        return [cid for cid, _ in rrf_fuse([lexical, semantic])]
