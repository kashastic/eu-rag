"""Server-side question quotas — the actual cost gates.

Two of them, with deliberately different shapes because they answer different
questions:

- `AnonQuota` — anonymous questions per key (client IP) **per UTC day**. A
  recurring allowance: the visitor has no account to attach a history to, so the
  only sane reset is time. Rotating IPs is mitigated by Turnstile at the
  anonymous boundary — see docs/DEPLOY.md.
- `UserQuota` — free questions per logged-in user, **for the lifetime of the
  account**. This one never resets: it is a one-time trial that ends in BYOK, so
  the server's key stops paying for a returning free user. There is no day
  column and nothing is ever swept.

Both live on the shared Database, so the limit holds across every API instance
and cannot be reset by clearing browser state. The frontend counters only
*reflect* these; they never enforce them.
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


_USER_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_quota (
    username TEXT PRIMARY KEY,
    used     INTEGER NOT NULL DEFAULT 0
);
"""


class UserQuota:
    """Lifetime free-question allowance for a logged-in user.

    Not a variant of AnonQuota with a different key — a different thing. There
    is no day column, so this never resets and nothing is ever swept: one row
    per account that ever asked a question, which is bounded by the user table.
    Spending it is the end of the free tier, and the only way past it is BYOK.

    A user who adds their own key stops consulting this counter entirely
    (`deps.paid_tier` returns tier "byok" and the caller skips the gate), so the
    count is frozen, not lost — removing the key returns them to whatever free
    questions were left.
    """

    def __init__(self, db: Database):
        self._db = db
        db.executescript(_USER_SCHEMA)

    def remaining(self, username: str, limit: int) -> int:
        row = self._db.query_one(
            "SELECT used FROM user_quota WHERE username = ?", (username,)
        )
        return max(0, limit - (row["used"] if row else 0))

    def erase_user(self, username: str) -> None:
        """Drop the lifetime counter along with the account.

        This does not weaken the free-tier cap. The cap is per *account*, and
        registering a second account has always started a fresh allowance — so
        keeping this row after an erasure would retain a username without
        buying any abuse resistance it doesn't already lack.
        """
        with self._db.transaction() as tx:
            tx.execute("DELETE FROM user_quota WHERE username = ?", (username,))

    def consume(self, username: str, limit: int) -> tuple[bool, int]:
        """Atomically take one from the lifetime allowance. Returns
        (allowed, remaining_after); when spent, returns (False, 0) and does not
        increment."""
        with self._db.transaction() as tx:
            row = self._db.query_one(
                "SELECT used FROM user_quota WHERE username = ?", (username,)
            )
            used = row["used"] if row else 0
            if used >= limit:
                return False, 0
            if row is None:
                tx.execute(
                    "INSERT INTO user_quota (username, used) VALUES (?, 1)", (username,)
                )
            else:
                tx.execute(
                    "UPDATE user_quota SET used = used + 1 WHERE username = ?", (username,)
                )
        return True, limit - used - 1

    def refund(self, username: str) -> None:
        """Give back one question consumed by a call that then failed. Guarded
        so a refund without a matching consume can never push the counter
        negative."""
        with self._db.transaction() as tx:
            tx.execute(
                "UPDATE user_quota SET used = used - 1 WHERE username = ? AND used > 0",
                (username,),
            )
