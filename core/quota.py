"""Server-side anonymous question quota — the actual cost gate.

Counts anonymous questions per key (client IP) per UTC day on the shared
Database, so the limit holds across every API instance and cannot be reset by
clearing browser state. The frontend popup only *reflects* this; it never
enforces it. A determined attacker rotating IPs is mitigated by a CAPTCHA
(Turnstile) at the anonymous boundary — see docs/DEPLOY.md.
"""

from datetime import date

from core.db import Database

_SCHEMA = """
CREATE TABLE IF NOT EXISTS anon_quota (
    quota_key TEXT NOT NULL,
    day       TEXT NOT NULL,
    used      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (quota_key, day)
);
"""


class AnonQuota:
    def __init__(self, db: Database):
        self._db = db
        db.executescript(_SCHEMA)

    def remaining(self, key: str, limit: int) -> int:
        row = self._db.query_one(
            "SELECT used FROM anon_quota WHERE quota_key = ? AND day = ?",
            (key, date.today().isoformat()),
        )
        return max(0, limit - (row["used"] if row else 0))

    def consume(self, key: str, limit: int) -> tuple[bool, int]:
        """Atomically take one from today's allowance. Returns
        (allowed, remaining_after). When the allowance is spent, returns
        (False, 0) and does not increment."""
        day = date.today().isoformat()
        with self._db.transaction() as tx:
            row = self._db.query_one(
                "SELECT used FROM anon_quota WHERE quota_key = ? AND day = ?",
                (key, day),
            )
            used = row["used"] if row else 0
            if used >= limit:
                return False, 0
            if row is None:
                tx.execute(
                    "INSERT INTO anon_quota (quota_key, day, used) VALUES (?, ?, 1)",
                    (key, day),
                )
            else:
                tx.execute(
                    "UPDATE anon_quota SET used = used + 1 WHERE quota_key = ? AND day = ?",
                    (key, day),
                )
        return True, limit - used - 1
