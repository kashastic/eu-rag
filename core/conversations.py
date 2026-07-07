"""Saved chats: conversations and their messages, scoped to a user.

Runs on the shared Database (SQLite locally, Postgres in production), so a
user's chat history is identical on every app instance. Citations are stored
as a JSON blob alongside each assistant message so a reopened chat renders
exactly as it did live.
"""

from __future__ import annotations

import json
import time
import uuid

from core.db import Database

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id         TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    title      TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS conversations_user ON conversations(username, updated_at);
CREATE TABLE IF NOT EXISTS messages (
    id              {serial},
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    citations       TEXT NOT NULL DEFAULT '[]',
    meta            TEXT NOT NULL DEFAULT '{}',
    created_at      DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id, id);
"""


class ConversationStore:
    def __init__(self, db: Database):
        self._db = db
        # DOUBLE PRECISION is Postgres; SQLite treats unknown types as NUMERIC,
        # which stores Python floats fine — so the same DDL runs on both.
        db.executescript(_SCHEMA)

    def create(self, username: str, title: str = "New chat") -> dict:
        now = time.time()
        conv_id = "conv_" + uuid.uuid4().hex[:20]
        with self._db.transaction() as tx:
            tx.execute(
                "INSERT INTO conversations (id, username, title, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (conv_id, username, title.strip()[:120] or "New chat", now, now),
            )
        return {"id": conv_id, "title": title, "created_at": now, "updated_at": now}

    def list(self, username: str) -> list[dict]:
        return self._db.query(
            "SELECT id, title, created_at, updated_at FROM conversations"
            " WHERE username = ? ORDER BY updated_at DESC",
            (username,),
        )

    def owner(self, conv_id: str) -> str | None:
        row = self._db.query_one(
            "SELECT username FROM conversations WHERE id = ?", (conv_id,)
        )
        return row["username"] if row else None

    def get(self, conv_id: str, username: str) -> dict | None:
        conv = self._db.query_one(
            "SELECT id, title, created_at, updated_at FROM conversations"
            " WHERE id = ? AND username = ?",
            (conv_id, username),
        )
        if conv is None:
            return None
        rows = self._db.query(
            "SELECT role, content, citations, meta, created_at FROM messages"
            " WHERE conversation_id = ? ORDER BY id",
            (conv_id,),
        )
        conv["messages"] = [
            {
                "role": r["role"],
                "content": r["content"],
                "citations": json.loads(r["citations"]),
                "meta": json.loads(r["meta"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]
        return conv

    def add_message(
        self,
        conv_id: str,
        role: str,
        content: str,
        citations: list | None = None,
        meta: dict | None = None,
    ) -> None:
        now = time.time()
        with self._db.transaction() as tx:
            tx.execute(
                "INSERT INTO messages (conversation_id, role, content, citations, meta,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    conv_id,
                    role,
                    content,
                    json.dumps(citations or []),
                    json.dumps(meta or {}),
                    now,
                ),
            )
            tx.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conv_id)
            )

    def rename(self, conv_id: str, username: str, title: str) -> bool:
        cur = self._db.execute(
            "UPDATE conversations SET title = ? WHERE id = ? AND username = ?",
            (title.strip()[:120] or "Untitled", conv_id, username),
        )
        if not self._db.is_pg:
            self._db._conn.commit()
        return cur.rowcount > 0

    def delete(self, conv_id: str, username: str) -> bool:
        with self._db.transaction() as tx:
            cur = tx.execute(
                "DELETE FROM conversations WHERE id = ? AND username = ?",
                (conv_id, username),
            )
            deleted = cur.rowcount > 0
            if deleted:
                tx.execute(
                    "DELETE FROM messages WHERE conversation_id = ?", (conv_id,)
                )
        return deleted

    def erase_user(self, username: str) -> int:
        ids = [c["id"] for c in self.list(username)]
        with self._db.transaction() as tx:
            for conv_id in ids:
                tx.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
            tx.execute("DELETE FROM conversations WHERE username = ?", (username,))
        return len(ids)
