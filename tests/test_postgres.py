"""Postgres parity for the shared multi-instance stores.

Opt-in: set EURAG_TEST_DATABASE_URL to a throwaway Postgres, e.g.
    docker run -d --name pg -e POSTGRES_PASSWORD=eurag -e POSTGRES_DB=eurag \\
        -p 55432:5432 postgres:16-alpine
    EURAG_TEST_DATABASE_URL=postgresql://postgres:eurag@localhost:55432/eurag \\
        pytest tests/test_postgres.py
Skips (never fails CI) when the variable is unset. Each test uses a unique
schema-ish username prefix so reruns against the same DB don't collide.
"""

import os
import uuid

import pytest

from core.conversations import ConversationStore
from core.db import Database
from core.profile import BusinessProfile
from core.security.auth import AuthError, AuthStore

URL = os.environ.get("EURAG_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not URL, reason="set EURAG_TEST_DATABASE_URL to run Postgres parity tests"
)


@pytest.fixture()
def db():
    d = Database(URL)
    yield d
    d.close()


def _u(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def test_auth_on_postgres(db):
    auth = AuthStore(db, "x" * 40)
    name = _u("alice")
    p = auth.register(name, "longpassword1")
    assert p.tenant == name
    tokens = auth.issue_tokens(p)
    assert auth.verify_access(tokens["access_token"]).username == name
    auth.refresh(tokens["refresh_token"])
    with pytest.raises(AuthError):
        auth.refresh(tokens["refresh_token"])  # single-use across the fleet


def test_conversations_on_postgres(db):
    conv = ConversationStore(db)
    owner = _u("bob")
    c = conv.create(owner, "chat")
    conv.add_message(c["id"], "user", "q")
    conv.add_message(
        c["id"], "assistant", "a [1].", citations=[{"marker": 1, "title": "X"}]
    )
    full = conv.get(c["id"], owner)
    assert [m["role"] for m in full["messages"]] == ["user", "assistant"]
    assert full["messages"][1]["citations"][0]["title"] == "X"
    assert conv.get(c["id"], _u("intruder")) is None  # isolation holds on PG


def test_authstore_migrates_a_pre_google_table(db):
    """The upgrade path, on the backend prod actually runs.

    This is the case that took the live API down: `executescript` sends the
    whole schema to Postgres as ONE statement, so a single failing line aborts
    all of it — and a `CREATE UNIQUE INDEX ... (google_sub)` placed before the
    ALTER that adds `google_sub` fails on every database that already existed.
    A fresh DB cannot reproduce it, because there the CREATE TABLE already has
    the column.
    """
    table = "users"
    db.executescript("DROP TABLE IF EXISTS users CASCADE;")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            username     TEXT PRIMARY KEY,
            salt         TEXT NOT NULL,
            pw_hash      TEXT NOT NULL,
            role         TEXT NOT NULL,
            tenant       TEXT NOT NULL,
            created_at   DOUBLE PRECISION NOT NULL,
            byok_key_enc TEXT
        );
        """
    )
    db.execute(
        "INSERT INTO users (username, salt, pw_hash, role, tenant, created_at)"
        " VALUES ('legacyuser', 'aa', 'bb', 'admin', 'legacyuser', 1.0)"
    )

    store = AuthStore(db, "s" * 40)  # must not raise
    row = db.query_one("SELECT * FROM users WHERE username = 'legacyuser'")
    assert row["google_sub"] is None and row["byok_set_at"] is None
    # the profile columns are the newest link in the same ALTER chain
    assert row["profile_country"] is None and row["profile_ai_role"] is None
    assert store.upsert_google_user("pg-sub-1", "pguser@example.com").username == "pguser"
    store.set_profile("legacyuser", BusinessProfile(country="FR", size="medium"))
    assert store.get_profile("legacyuser").size == "medium"
    names = {r["indexname"] for r in db.query(
        "SELECT indexname FROM pg_indexes WHERE tablename = %s", (table,)
    )}
    assert "users_google_sub" in names
    AuthStore(db, "s" * 40)  # a redeploy re-runs this — idempotent


def test_authstore_migrates_the_currently_deployed_table(db):
    """The other migration test starts from the *pre-Google* shape, which is
    two releases behind what is actually running. The upgrade that matters on
    any given deploy is the one from the shape in production right now — here,
    google_sub and email present, profile columns absent.

    Worth its own test because the shapes differ in what has already run: this
    one starts with `users_google_sub` already created, so it also proves the
    guarded `CREATE UNIQUE INDEX` is genuinely idempotent against a real
    Postgres rather than only against a fresh database.
    """
    db.executescript("DROP TABLE IF EXISTS users CASCADE;")
    db.executescript(
        """
        CREATE TABLE users (
            username     TEXT PRIMARY KEY,
            salt         TEXT NOT NULL,
            pw_hash      TEXT NOT NULL,
            role         TEXT NOT NULL,
            tenant       TEXT NOT NULL,
            created_at   DOUBLE PRECISION NOT NULL,
            byok_key_enc TEXT,
            byok_set_at  DOUBLE PRECISION,
            google_sub   TEXT,
            email        TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS users_google_sub ON users (google_sub);
        """
    )
    db.execute(
        "INSERT INTO users (username, salt, pw_hash, role, tenant, created_at)"
        " VALUES ('pwuser', 'aa', 'bb', 'admin', 'pwuser', 1.0)"
    )
    # a Google account stores an empty pw_hash — it must survive the ALTERs too
    db.execute(
        "INSERT INTO users (username, salt, pw_hash, role, tenant, created_at,"
        " google_sub, email) VALUES ('goog', 'cc', '', 'user', 'goog', 2.0,"
        " 'sub-123', 'g@example.com')"
    )

    store = AuthStore(db, "s" * 40)  # the boot path — must not raise

    assert db.query_one("SELECT * FROM users WHERE username = 'pwuser'")["profile_country"] is None
    assert store.get_profile("pwuser") == BusinessProfile()
    store.set_profile("pwuser", BusinessProfile(country="DE", ai_role="provider"))
    assert store.get_profile("pwuser").ai_role == "provider"
    # the existing Google identity still resolves on google_sub after migrating
    assert store.upsert_google_user("sub-123", "g@example.com").username == "goog"
    AuthStore(db, "s" * 40)  # redeploy re-runs it
