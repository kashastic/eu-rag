"""Small dialect-aware database layer: SQLite for local dev, PostgreSQL for
multi-instance production.

`EURAG_DATABASE_URL=postgresql://…` routes the shared mutable stores (users,
refresh tokens, audit, conversations) to Postgres so every app instance sees
the same state — the prerequisite for horizontal scaling behind a load
balancer. Unset (or `sqlite://…`) keeps everything in a local SQLite file.

Scope: SQL is written once with `?` placeholders and `{serial}` /
`{ts_default}` schema tokens that each backend fills in. This is intentionally
minimal — the stores use plain CRUD, not an ORM.
"""

import os
import re
from contextlib import contextmanager
from pathlib import Path

_SQLITE_SERIAL = "INTEGER PRIMARY KEY AUTOINCREMENT"
_PG_SERIAL = "BIGSERIAL PRIMARY KEY"


def database_url() -> str | None:
    url = os.environ.get("EURAG_DATABASE_URL", "").strip()
    return url or None


def is_postgres(url: str | None) -> bool:
    return bool(url) and url.startswith(("postgres://", "postgresql://"))


class Database:
    """Thin wrapper exposing execute/query with `?` placeholders on both
    backends. Rows come back as dicts."""

    def __init__(self, url: str | None, sqlite_path: Path | None = None):
        self.is_pg = is_postgres(url)
        if self.is_pg:
            import psycopg
            from psycopg.rows import dict_row

            self._conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)
        else:
            import sqlite3

            path = sqlite_path or Path("var/eurag.sqlite3")
            path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode = WAL")

    def _adapt(self, sql: str) -> str:
        if self.is_pg:
            sql = sql.replace("?", "%s")
            sql = sql.replace("{serial}", _PG_SERIAL)
        else:
            sql = sql.replace("{serial}", _SQLITE_SERIAL)
        return sql

    def executescript(self, script: str) -> None:
        script = script.replace("{serial}", _PG_SERIAL if self.is_pg else _SQLITE_SERIAL)
        if self.is_pg:
            with self._conn.cursor() as cur:
                cur.execute(script)
        else:
            self._conn.executescript(script)

    def execute(self, sql: str, params: tuple = ()):  # returns rowcount-bearing cursor
        cur = self._conn.cursor()
        cur.execute(self._adapt(sql), params)
        return cur

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        cur = self._conn.cursor()
        cur.executemany(self._adapt(sql), rows)
        if not self.is_pg:
            self._conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        cur = self.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    def query_one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @contextmanager
    def transaction(self):
        if self.is_pg:
            self._conn.autocommit = False
            try:
                yield self
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                self._conn.autocommit = True
        else:
            try:
                yield self
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        self._conn.close()
