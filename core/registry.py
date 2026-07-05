"""SQLite document/chunk registry — the system of record for provenance.

Embedded now; the schema is plain SQL so Postgres in M4 is a driver swap.
"""

import sqlite3
from pathlib import Path

from core.ingestion.chunker import Chunk
from core.ingestion.document_loader import Document

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id       TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    source_url   TEXT NOT NULL DEFAULT '',
    source_type  TEXT NOT NULL,
    language     TEXT NOT NULL,
    fetched_at   TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    n_chunks     INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id   TEXT PRIMARY KEY,
    doc_id     TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    idx        INTEGER NOT NULL,
    text       TEXT NOT NULL,
    title      TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
"""


class Registry:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)

    def document_hash(self, doc_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT content_hash FROM documents WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        return row["content_hash"] if row else None

    def save(self, doc: Document, chunks: list[Chunk]) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc.doc_id,))
            self._conn.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    doc.doc_id,
                    doc.title,
                    doc.source_url,
                    doc.source_type,
                    doc.language,
                    doc.fetched_at,
                    doc.content_hash,
                    len(chunks),
                ),
            )
            self._conn.executemany(
                "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (c.chunk_id, c.doc_id, c.index, c.text, c.title, c.source_url)
                    for c in chunks
                ],
            )

    def all_chunks(self) -> list[Chunk]:
        rows = self._conn.execute("SELECT * FROM chunks").fetchall()
        return [self._row_to_chunk(r) for r in rows]

    def get_chunks(self, chunk_ids: list[str]) -> list[Chunk]:
        if not chunk_ids:
            return []
        placeholders = ",".join("?" * len(chunk_ids))
        rows = self._conn.execute(
            f"SELECT * FROM chunks WHERE chunk_id IN ({placeholders})", chunk_ids
        ).fetchall()
        by_id = {r["chunk_id"]: self._row_to_chunk(r) for r in rows}
        return [by_id[cid] for cid in chunk_ids if cid in by_id]

    def list_documents(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT doc_id, title, source_url, source_type, language, fetched_at,"
            " n_chunks FROM documents ORDER BY title"
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _row_to_chunk(row: sqlite3.Row) -> Chunk:
        return Chunk(
            chunk_id=row["chunk_id"],
            doc_id=row["doc_id"],
            index=row["idx"],
            text=row["text"],
            title=row["title"],
            source_url=row["source_url"],
        )

    def close(self) -> None:
        self._conn.close()
