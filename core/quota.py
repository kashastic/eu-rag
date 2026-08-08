"""Server-side anonymous question quota — the actual cost gate.

Counts anonymous questions per key (client IP) per UTC day on the shared
Database, so the limit holds across every API instance and cannot be reset by
clearing browser state. The frontend popup only *reflects* this; it never
enforces it. A determined attacker rotating IPs is mitigated by a CAPTCHA
(Turnstile) at the anonymous boundary — see docs/DEPLOY.md.
"""

from datetime import date, timedelta

from core.db import Database

# Rows older than this are dead weight — the counter only ever reads *today*.
# Kept for a couple of days so a UTC-boundary request or a manual audit still
# sees yesterday. Swept opportunistically on consume (see _sweep).
_RETAIN_DAYS = 2

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
                # first question of the day for this key — a rare enough path
                # to piggyback the sweep on, so steady-state consumes stay a
                # single UPDATE. Without it the table grows one row per IP/day
                # forever (no cron in this deployment).
                self._sweep(tx, day)
            else:
                tx.execute(
                    "UPDATE anon_quota SET used = used + 1 WHERE quota_key = ? AND day = ?",
                    (key, day),
                )
        return True, limit - used - 1

    @staticmethod
    def _sweep(tx, day: str) -> None:
        """Drop expired rows. `day` is stored as an ISO date string, so a plain
        string comparison is a correct date comparison on both SQLite and
        Postgres."""
        cutoff = (date.fromisoformat(day) - timedelta(days=_RETAIN_DAYS)).isoformat()
        tx.execute("DELETE FROM anon_quota WHERE day < ?", (cutoff,))

    def refund(self, key: str) -> None:
        """Give back one question consumed today — the LLM call failed after
        the consume, and the user got no answer for it. Guarded so a refund
        without a matching consume can never push the counter negative."""
        with self._db.transaction() as tx:
            tx.execute(
                "UPDATE anon_quota SET used = used - 1 "
                "WHERE quota_key = ? AND day = ? AND used > 0",
                (key, date.today().isoformat()),
            )
