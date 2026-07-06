"""Qdrant wrapper: embedded local mode by default, server when QDRANT_URL is set."""

import uuid
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client import models as qm

from core.ingestion.chunker import Chunk

COLLECTION = "eurag_chunks"


def _point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"eurag:{chunk_id}"))


class VectorStore:
    def __init__(self, dim: int, path: Path | None = None, url: str | None = None):
        if url:
            self._client = QdrantClient(url=url)
        else:
            path = path or Path("var/qdrant")
            path.mkdir(parents=True, exist_ok=True)
            self._client = QdrantClient(path=str(path))
        self._ensure_collection(dim)

    def _ensure_collection(self, dim: int) -> None:
        if self._client.collection_exists(COLLECTION):
            info = self._client.get_collection(COLLECTION)
            if info.config.params.vectors.size == dim:
                return
            # embedder changed → stored vectors are meaningless; rebuild
            self._client.delete_collection(COLLECTION)
        self._client.create_collection(
            COLLECTION,
            vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
        )

    def upsert(
        self, chunks: list[Chunk], vectors: list[list[float]], tenant: str = "public"
    ) -> None:
        points = [
            qm.PointStruct(
                id=_point_id(chunk.chunk_id),
                vector=vector,
                payload={
                    "chunk_id": chunk.chunk_id,
                    "doc_id": chunk.doc_id,
                    "tenant": tenant,
                },
            )
            for chunk, vector in zip(chunks, vectors)
        ]
        self._client.upsert(COLLECTION, points=points)

    def delete_document(self, doc_id: str) -> None:
        self._client.delete(
            COLLECTION,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(
                    must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))]
                )
            ),
        )

    def search(
        self, vector: list[float], k: int = 10, tenants: list[str] | None = None
    ) -> list[tuple[str, float]]:
        query_filter = None
        if tenants is not None:
            query_filter = qm.Filter(
                must=[qm.FieldCondition(key="tenant", match=qm.MatchAny(any=tenants))]
            )
        hits = self._client.query_points(
            COLLECTION, query=vector, limit=k, query_filter=query_filter
        ).points
        return [(hit.payload["chunk_id"], hit.score) for hit in hits]

    def close(self) -> None:
        self._client.close()
