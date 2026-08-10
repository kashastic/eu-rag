import os
import time

import jwt
import pytest

from core.db import Database
from core.profile import BusinessProfile
from core.security.auth import AuthError, AuthStore, question_hash

SECRET = "x" * 40  # ≥32 bytes, silences the jwt short-key warning


@pytest.fixture()
def store(tmp_path):
    from core.db import Database

    s = AuthStore(Database(None, sqlite_path=tmp_path / "eurag.db"), SECRET)
    yield s
    s.close()


def test_first_user_is_admin_rest_are_users(store):
    assert store.register("alice", "longpassword1").role == "admin"
    assert store.register("bob", "longpassword2").role == "user"
    assert store.register("carol", "longpassword3").role == "user"


def test_each_user_gets_private_tenant(store):
    assert store.register("alice", "longpassword1").tenant == "alice"
    assert store.register("bob", "longpassword2").tenant == "bob"


def test_duplicate_username_rejected(store):
    store.register("alice", "longpassword1")
    with pytest.raises(AuthError, match="already taken"):
        store.register("alice", "longpassword2")


def test_weak_password_and_bad_username_rejected(store):
    with pytest.raises(AuthError, match="10 characters"):
        store.register("alice", "short")
    with pytest.raises(AuthError, match="letters/digits"):
        store.register("a b", "longpassword1")


def test_authenticate_roundtrip_and_wrong_password(store):
    store.register("alice", "longpassword1")
    assert store.authenticate("alice", "longpassword1").username == "alice"
    with pytest.raises(AuthError, match="invalid credentials"):
        store.authenticate("alice", "wrongpassword")


def test_access_token_carries_identity(store):
    p = store.register("alice", "longpassword1")
    tokens = store.issue_tokens(p)
    verified = store.verify_access(tokens["access_token"])
    assert (verified.username, verified.role, verified.tenant) == ("alice", "admin", "alice")


def test_refresh_token_is_single_use(store):
    p = store.register("alice", "longpassword1")
    tokens = store.issue_tokens(p)
    rotated = store.refresh(tokens["refresh_token"])
    assert "access_token" in rotated
    # the original refresh token is now dead — stolen-token reuse fails
    with pytest.raises(AuthError, match="reused"):
        store.refresh(tokens["refresh_token"])


def test_access_token_rejected_as_refresh(store):
    tokens = store.issue_tokens(store.register("alice", "longpassword1"))
    with pytest.raises(AuthError, match="not a refresh token"):
        store.refresh(tokens["access_token"])


def test_expired_token_rejected(store):
    p = store.register("alice", "longpassword1")
    stale = jwt.encode(
        {"sub": "alice", "role": "admin", "tenant": "alice", "type": "access",
         "exp": int(time.time()) - 1},
        SECRET, algorithm="HS256",
    )
    with pytest.raises(AuthError):
        store.verify_access(stale)


def test_token_signed_with_other_secret_rejected(store):
    forged = jwt.encode(
        {"sub": "attacker", "role": "admin", "tenant": "public", "type": "access",
         "exp": int(time.time()) + 3600},
        "different-secret-entirely-abc", algorithm="HS256",
    )
    with pytest.raises(AuthError):
        store.verify_access(forged)


def test_audit_appends_and_exposes_no_mutation(store):
    store.audit("alice", "query", detail="abc123")
    store.audit("alice", "auth.login")
    actions = [e["action"] for e in store.audit_entries()]
    assert actions == ["auth.login", "query"]  # newest first, both retained
    # append-only by discipline: the store offers no update/delete for audit
    assert not hasattr(store, "delete_audit")
    assert not hasattr(store, "update_audit")


def test_question_hash_is_not_reversible():
    h = question_hash("Do I need a DPO?")
    assert "DPO" not in h and len(h) == 16


# --- upgrading an EXISTING database ---------------------------------------
# Every other test here starts from an empty DB, where CREATE TABLE builds the
# current shape and any migration is a no-op. That is exactly the case that
# cannot catch a bad migration. These start from an old table on purpose.

_PRE_GOOGLE_SCHEMA = """
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


def _legacy_db(tmp_path):
    """A database as it existed before byok_set_at / google_sub / email."""
    db = Database(None, sqlite_path=tmp_path / "legacy.db")
    db.executescript(_PRE_GOOGLE_SCHEMA)
    db.execute(
        "INSERT INTO users (username, salt, pw_hash, role, tenant, created_at)"
        " VALUES ('alice', ?, ?, 'admin', 'alice', 1.0)",
        (os.urandom(16).hex(), "deadbeef"),
    )
    db._conn.commit()
    db.close()
    return Database(None, sqlite_path=tmp_path / "legacy.db")


def test_opening_a_pre_google_database_migrates_instead_of_exploding(tmp_path):
    """Regression: the google_sub unique index used to live in _SCHEMA, which
    runs BEFORE the ALTERs that add the column. On a fresh DB the CREATE TABLE
    already had the column so it passed; on an existing one it raised
    'no such column: google_sub' out of AuthStore.__init__, which took the whole
    API down at boot. Anything referencing a new column must run after the
    ALTERs."""
    store = AuthStore(_legacy_db(tmp_path), "s" * 32)
    # the pre-existing account survived and still works
    row = store._db.query_one("SELECT * FROM users WHERE username = 'alice'")
    assert row["google_sub"] is None and row["email"] is None
    assert row["byok_set_at"] is None
    # every later column arrives the same way — the profile fields are the
    # newest, so this asserts the whole ALTER chain ran, not just its head
    assert row["profile_country"] is None and row["profile_ai_role"] is None
    # ...and the new paths work on the migrated table
    principal = store.upsert_google_user("sub-1", "bob@example.com")
    assert principal.username == "bob"
    store.set_byok("alice", "enc-blob")
    assert store.byok_set_at("alice") > 0
    store.set_profile("alice", BusinessProfile(country="DE", ai_role="provider"))
    assert store.get_profile("alice").country == "DE"


def test_migration_is_idempotent(tmp_path):
    """Boot, boot again — every deploy re-runs this path."""
    db_path = tmp_path / "legacy.db"
    AuthStore(_legacy_db(tmp_path), "s" * 32)
    for _ in range(2):
        store = AuthStore(Database(None, sqlite_path=db_path), "s" * 32)
        assert store.register("carol" + str(_), "longpassword1").username
