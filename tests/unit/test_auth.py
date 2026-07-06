import sqlite3
import time

import jwt
import pytest

from core.security.auth import AuthError, AuthStore, question_hash

SECRET = "x" * 40  # ≥32 bytes, silences the jwt short-key warning


@pytest.fixture()
def store(tmp_path):
    s = AuthStore(tmp_path / "auth.db", SECRET)
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


def test_audit_is_append_only(store):
    store.audit("alice", "query", detail="abc123")
    assert store.audit_entries()[0]["action"] == "query"
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute("DELETE FROM audit")
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute("UPDATE audit SET action = 'x'")


def test_question_hash_is_not_reversible():
    h = question_hash("Do I need a DPO?")
    assert "DPO" not in h and len(h) == 16
