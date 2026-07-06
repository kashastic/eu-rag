"""SQLite document/chunk registry — the system of record for provenance.

Embedded now; the schema is plain SQL so Postgres in M4 is a driver swap.

Two security responsibilities live here because this is the one place chunk
text is written and read:
- **Tenancy**: every document belongs to a tenant. The shared official corpus
  is tenant "public"; each user's uploads live in their own tenant. Reads are
  scoped to a set of allowed tenants — `get_chunks` is the hard gate that
  drops any chunk_id outside them, so a retrieval leak upstream cannot surface
  another tenant's text.
- **At-rest encryption**: if a cipher is configured, chunk text is encrypted
  on write and decrypted on read, transparently. Titles and source URLs stay
  plaintext (they are the citation surface and public for this corpus).
"""

import sqlite3
from pathlib import Path

from core.ingestion.chunker import Chunk
from core.ingestion.document_loader import Document

PUBLIC_TENANT = "public"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id       TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    source_url   TEXT NOT NULL DEFAULT '',
    source_type  TEXT NOT NULL,
    language     TEXT NOT NULL,
    fetched_at   TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    n_chunks     INTEGER NOT NULL,
    tenant       TEXT NOT NULL DEFAULT 'public'
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id   TEXT PRIMARY KEY,
    doc_id     TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    idx        INTEGER NOT NULL,
    text       TEXT NOT NULL,
    title      TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    tenant     TEXT NOT NULL DEFAULT 'public'
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS chunks_tenant ON chunks(tenant);
"""


class Registry:
    def __init__(self, path: Path, cipher=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._cipher = cipher

    def _decrypt(self, stored: str) -> str:
        return self._cipher.decrypt(stored) if self._cipher else stored

    def _encrypt(self, plaintext: str) -> str:
        return self._cipher.encrypt(plaintext) if self._cipher else plaintext

    def document_hash(self, doc_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT content_hash FROM documents WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        return row["content_hash"] if row else None

    def save(self, doc: Document, chunks: list[Chunk], tenant: str = PUBLIC_TENANT) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc.doc_id,))
            self._conn.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    doc.doc_id,
                    doc.title,
                    doc.source_url,
                    doc.source_type,
                    doc.language,
                    doc.fetched_at,
                    doc.content_hash,
                    len(chunks),
                    tenant,
                ),
            )
            self._conn.executemany(
                "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        c.chunk_id,
                        c.doc_id,
                        c.index,
                        self._encrypt(c.text),
                        c.title,
                        c.source_url,
                        tenant,
                    )
                    for c in chunks
                ],
            )

    def all_chunks(self) -> list[Chunk]:
        rows = self._conn.execute("SELECT * FROM chunks").fetchall()
        return [self._row_to_chunk(r) for r in rows]

    def get_chunks(self, chunk_ids: list[str], tenants: list[str] | None = None) -> list[Chunk]:
        """Materialize chunk_ids to Chunks, scoped to `tenants` when given.
        This is the hard tenant gate: an id outside the allowed tenants is
        silently dropped, so an upstream retrieval leak cannot surface text."""
        if not chunk_ids:
            return []
        placeholders = ",".join("?" * len(chunk_ids))
        sql = f"SELECT * FROM chunks WHERE chunk_id IN ({placeholders})"
        params = list(chunk_ids)
        if tenants is not None:
            sql += f" AND tenant IN ({','.join('?' * len(tenants))})"
            params += tenants
        rows = self._conn.execute(sql, params).fetchall()
        by_id = {r["chunk_id"]: self._row_to_chunk(r) for r in rows}
        return [by_id[cid] for cid in chunk_ids if cid in by_id]

    def list_documents(self, tenants: list[str] | None = None) -> list[dict]:
        sql = (
            "SELECT doc_id, title, source_url, source_type, language, fetched_at,"
            " n_chunks, tenant FROM documents"
        )
        params: list = []
        if tenants is not None:
            sql += f" WHERE tenant IN ({','.join('?' * len(tenants))})"
            params = list(tenants)
        rows = self._conn.execute(sql + " ORDER BY title", params).fetchall()
        return [dict(r) for r in rows]

    def document_tenant(self, doc_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT tenant FROM documents WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        return row["tenant"] if row else None

    def delete_document(self, doc_id: str) -> bool:
        """Remove a document and its chunks (GDPR Art. 17). Vectors are the
        pipeline's responsibility. Returns True if something was deleted."""
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM documents WHERE doc_id = ?", (doc_id,)
            )
        return cursor.rowcount > 0

    def tenant_doc_ids(self, tenant: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT doc_id FROM documents WHERE tenant = ?", (tenant,)
        ).fetchall()
        return [r["doc_id"] for r in rows]

    def _row_to_chunk(self, row: sqlite3.Row) -> Chunk:
        return Chunk(
            chunk_id=row["chunk_id"],
            doc_id=row["doc_id"],
            index=row["idx"],
            text=self._decrypt(row["text"]),
            title=row["title"],
            source_url=row["source_url"],
        )

    def close(self) -> None:
        self._conn.close()
