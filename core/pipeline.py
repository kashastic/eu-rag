"""Wires the pipeline together: one object the API (and seed script) talk to."""

import logging

from core.config import Settings, get_settings
from core.generation.answerer import AnswerResult, answer_question
from core.generation.llm_client import ExtractiveClient, get_llm_client
from core.ingestion.chunker import chunk_document
from core.ingestion.document_loader import Document
from core.ingestion.embedder import get_embedder
from core.registry import PUBLIC_TENANT, Registry
from core.retrieval.bm25 import BM25Index
from core.retrieval.hybrid_retriever import HybridRetriever
from core.retrieval.reranker import get_reranker
from core.retrieval.vector_store import VectorStore
from core.security import pii
from core.security.crypto import get_cipher

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.embedder = get_embedder(self.settings.embedder, self.settings.embed_model)
        self.registry = Registry(
            self.settings.registry_path,
            cipher=get_cipher(self.settings.encryption_key),
        )
        self.vectors = VectorStore(
            dim=self.embedder.dim,
            path=self.settings.qdrant_path,
            url=self.settings.qdrant_url,
        )
        self.bm25 = BM25Index()
        self.llm = get_llm_client(self.settings.llm_model)
        # low-confidence cascade: cheap model answers everything, this one is
        # consulted only when the cheap answer signals insufficiency
        self.escalation_llm = None
        if not isinstance(self.llm, ExtractiveClient) and self.settings.escalation_model not in (
            "",
            "none",
            self.settings.llm_model,
        ):
            candidate = get_llm_client(self.settings.escalation_model)
            if not isinstance(candidate, ExtractiveClient):
                self.escalation_llm = candidate
        expander = decomposer = None
        if not isinstance(self.llm, ExtractiveClient):
            from core.retrieval.expansion import HydeExpander, QueryDecomposer

            if self.settings.hyde_model not in ("", "none"):
                expander = HydeExpander(get_llm_client(self.settings.hyde_model))
            if self.settings.decompose_model not in ("", "none"):
                decomposer = QueryDecomposer(
                    get_llm_client(self.settings.decompose_model)
                )
        self.retriever = HybridRetriever(
            self.bm25,
            self.vectors,
            self.embedder,
            reranker=get_reranker(self.settings.reranker),
            get_chunks=self.registry.get_chunks,
            expander=expander,
            decomposer=decomposer,
        )
        self._rebuild_bm25()

    def _rebuild_bm25(self) -> None:
        chunks = self.registry.all_chunks()
        for chunk in chunks:
            self.bm25.add(chunk.chunk_id, chunk.text)
        logger.info("BM25 index rebuilt over %d chunks", len(chunks))

    def ingest(self, doc: Document, tenant: str = PUBLIC_TENANT) -> int:
        """Chunk, embed, and index a document. Returns chunk count.
        Re-ingesting unchanged content is a no-op (content hash match).

        The PII gate runs BEFORE any chunking or embedding — a rejected
        upload never touches the vector store — and skips official sources.
        Raises pii.PIIError on personal data in a user upload."""
        pii.gate(doc.text, doc.source_type, backend=self.settings.pii_backend)
        if self.registry.document_hash(doc.doc_id) == doc.content_hash:
            logger.info("unchanged, skipping: %s", doc.title)
            return 0
        chunks = chunk_document(doc)
        vectors = self.embedder.embed_texts([c.text for c in chunks])
        self.vectors.delete_document(doc.doc_id)
        self.vectors.upsert(chunks, vectors, tenant=tenant)
        self.registry.save(doc, chunks, tenant=tenant)
        for chunk in chunks:
            self.bm25.add(chunk.chunk_id, chunk.text)
        logger.info("ingested %s (%d chunks) into tenant %s", doc.title, len(chunks), tenant)
        return len(chunks)

    def erase_document(self, doc_id: str) -> bool:
        """GDPR Art. 17: remove a document from registry, vectors, and the
        live BM25 index. Returns True if it existed. Caller handles authz."""
        if not self.registry.delete_document(doc_id):
            return False
        self.vectors.delete_document(doc_id)
        # BM25 has no doc index; drop every chunk id under this doc
        for chunk_id in [c for c in self.bm25.ids() if c.rsplit(":", 1)[0] == doc_id]:
            self.bm25.remove(chunk_id)
        logger.info("erased document %s", doc_id)
        return True

    def erase_tenant(self, tenant: str) -> int:
        """Erase every document in a tenant (user account deletion)."""
        doc_ids = self.registry.tenant_doc_ids(tenant)
        for doc_id in doc_ids:
            self.erase_document(doc_id)
        return len(doc_ids)

    def query(
        self,
        question: str,
        industry: str | None = None,
        tenants: list[str] | None = None,
    ) -> AnswerResult:
        """`tenants` scopes retrieval; None (the local default) searches
        everything. A logged-in user passes [their tenant, "public"]."""
        if industry:
            # research signal for corpus expansion: which sectors ask questions
            logger.info("query industry context: %s", industry)
        result = self._answer(
            question, self.llm, k=self.settings.top_k, industry=industry, tenants=tenants
        )
        if (
            result.insufficient
            and result.mode != "no_sources"  # empty index — nothing to widen
            and self.escalation_llm is not None
        ):
            logger.info(
                "low-confidence answer — escalating to %s over wider retrieval",
                self.escalation_llm.name,
            )
            # the diverse first pass failed, so the retry goes deep instead:
            # insufficiency usually means the right document was found but the
            # answering passage sat below the per-doc cap
            result = self._answer(
                question,
                self.escalation_llm,
                k=self.settings.escalation_top_k,
                max_per_doc=6,
                industry=industry,
                tenants=tenants,
            )
            result.escalated = True
        return result

    def _answer(
        self,
        question: str,
        llm,
        k: int,
        max_per_doc: int = 2,
        industry: str | None = None,
        tenants: list[str] | None = None,
    ) -> AnswerResult:
        chunk_ids = self.retriever.retrieve(
            question, k=k, max_per_doc=max_per_doc, tenants=tenants
        )
        chunks = self.registry.get_chunks(chunk_ids, tenants)
        return answer_question(question, chunks, llm, industry=industry)

    def close(self) -> None:
        self.vectors.close()
        self.registry.close()
