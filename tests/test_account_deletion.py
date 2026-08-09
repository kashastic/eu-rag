"""Self-service account erasure (GDPR Art. 17) — DELETE /account.

The interesting cases are not "does the row go away". They are:
  - erasure must not reach anyone else's data,
  - it must kill live sessions, not just the row,
  - the audit trail must survive it, pseudonymised, so deleting an account
    can't be used to erase the evidence of an attack,
  - and it must never be able to delete the public corpus, which is reachable
    because a user's tenant *is* their username and "public" is a legal
    username.
"""

import pytest
from fastapi.testclient import TestClient

from core.db import Database
from core.quota import UserQuota
from core.security.auth import ERASED_ACTOR, AuthError, AuthStore


@pytest.fixture()
def store(tmp_path):
    return AuthStore(Database(None, sqlite_path=tmp_path / "auth.db"), "s" * 32)


# --- unit: AuthStore.erase_user -------------------------------------------

def test_erase_user_removes_the_account_and_its_sessions(store):
    store.register("alice", "longpassword1")
    tokens = store.issue_tokens(store.authenticate("alice", "longpassword1"))

    store.erase_user("alice")

    assert store._db.query_one("SELECT 1 AS x FROM users WHERE username = ?", ("alice",)) is None
    # the refresh token is gone, not merely revoked — a live session dies with
    # the account rather than surviving until it expires
    assert store._db.query_one(
        "SELECT 1 AS x FROM refresh_tokens WHERE username = ?", ("alice",)
    ) is None
    with pytest.raises(Exception):
        store.refresh(tokens["refresh_token"])


def test_erase_user_pseudonymises_the_audit_trail_instead_of_deleting_it(store):
    store.register("alice", "longpassword1")
    with pytest.raises(Exception):
        store.authenticate("alice", "wrongpassword")  # leaves auth.login_failed

    before = store.audit_entries(100)
    assert any(e["actor"] == "alice" for e in before)

    store.erase_user("alice")

    after = store.audit_entries(100)
    # the record of what happened survives...
    assert any(e["action"] == "auth.login_failed" for e in after)
    assert any(e["action"] == "account.erase" for e in after)
    # ...but no row still names the person
    assert not any(e["actor"] == "alice" for e in after)
    assert all(e["actor"] == ERASED_ACTOR for e in after)


def test_erase_user_leaves_other_accounts_audit_rows_alone(store):
    store.register("alice", "longpassword1")
    store.register("bob", "longpassword1")

    store.erase_user("alice")

    actors = {e["actor"] for e in store.audit_entries(100)}
    assert "bob" in actors
    assert "alice" not in actors


def test_erased_username_can_be_registered_again(store):
    """Nothing is left behind holding the name — including the unique index on
    google_sub, which an earlier bug would have tripped over."""
    store.register("alice", "longpassword1")
    store.erase_user("alice")
    assert store.register("alice", "differentpassword1").username == "alice"


# --- unit: the quota row ---------------------------------------------------

def test_user_quota_erase_drops_the_lifetime_counter(tmp_path):
    q = UserQuota(Database(None, sqlite_path=tmp_path / "q.db"))
    q.consume("alice", 10)
    q.consume("bob", 10)
    assert q.remaining("alice", 10) == 9

    q.erase_user("alice")

    assert q.remaining("alice", 10) == 10  # row gone
    assert q.remaining("bob", 10) == 9  # untouched


# --- unit: reserved usernames ---------------------------------------------

@pytest.mark.parametrize("name", ["public", "PUBLIC", " Public ", "deleted_account"])
def test_reserved_usernames_cannot_be_registered(store, name):
    """A username becomes a tenant id and an audit actor, so these two collide
    with things the system already uses as identifiers."""
    with pytest.raises(AuthError, match="reserved"):
        store.register(name, "longpassword1")


def test_google_derived_username_skips_a_reserved_name(store):
    """Nothing stops someone owning public@ at their own Google domain."""
    p = store.upsert_google_user("sub-1", "public@example.com")
    assert p.username == "public2"
    assert p.tenant == "public2"


# --- unit: the public-corpus floor ----------------------------------------

def test_erase_tenant_refuses_the_public_corpus(seeded_pipeline):
    """A user's tenant is their username and "public" satisfies the username
    rule, so self-service deletion could otherwise reach the official corpus."""
    before = len(seeded_pipeline.registry.list_documents())
    assert before > 0
    with pytest.raises(ValueError):
        seeded_pipeline.erase_tenant("public")
    assert len(seeded_pipeline.registry.list_documents()) == before


# --- API ------------------------------------------------------------------

@pytest.fixture()
def auth_client(settings, monkeypatch):
    monkeypatch.setenv("EURAG_AUTH_ENABLED", "true")
    monkeypatch.setenv("EURAG_JWT_SECRET", "test-secret-at-least-32-bytes-long!!")
    from api.main import app

    with TestClient(app) as client:
        yield client


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _account(client, username):
    client.post("/auth/register", json={"username": username, "password": "longpassword1"})
    tokens = client.post(
        "/auth/login", json={"username": username, "password": "longpassword1"}
    ).json()
    return tokens


def _delete(client, token, confirm):
    # httpx's .delete() takes no json= — a DELETE with a body has to go
    # through .request(). The browser client uses fetch, which is fine with it.
    return client.request(
        "DELETE", "/account", json={"confirm_username": confirm}, headers=_bearer(token)
    )


def test_delete_account_requires_a_token(auth_client):
    assert auth_client.request(
        "DELETE", "/account", json={"confirm_username": "alice"}
    ).status_code == 401


def test_delete_account_requires_the_typed_username(auth_client):
    tokens = _account(auth_client, "alice")
    res = _delete(auth_client, tokens["access_token"], "alicf")
    assert res.status_code == 422
    # and the account still works
    assert auth_client.get("/account", headers=_bearer(tokens["access_token"])).status_code == 200


def test_delete_account_erases_chats_and_kills_the_session(auth_client):
    tokens = _account(auth_client, "alice")
    access, refresh = tokens["access_token"], tokens["refresh_token"]
    auth_client.post("/conversations", json={}, headers=_bearer(access))
    auth_client.post("/conversations", json={}, headers=_bearer(access))

    res = _delete(auth_client, access, "alice")
    assert res.status_code == 200
    assert res.json()["conversations_erased"] == 2

    # the access token is a stateless JWT and stays *cryptographically* valid
    # until it expires, but every route that loads the user must now fail
    assert auth_client.get("/account", headers=_bearer(access)).status_code == 401
    assert auth_client.get("/conversations", headers=_bearer(access)).status_code == 401
    # and the session cannot be renewed
    assert auth_client.post("/auth/refresh", json={"refresh_token": refresh}).status_code == 401
    # nor signed back into
    assert auth_client.post(
        "/auth/login", json={"username": "alice", "password": "longpassword1"}
    ).status_code == 401


def test_deleting_one_account_leaves_another_intact(auth_client):
    alice = _account(auth_client, "alice")
    bob = _account(auth_client, "bob")
    bob_chat = auth_client.post(
        "/conversations", json={}, headers=_bearer(bob["access_token"])
    ).json()

    _delete(auth_client, alice["access_token"], "alice")

    assert auth_client.get("/account", headers=_bearer(bob["access_token"])).status_code == 200
    chats = auth_client.get("/conversations", headers=_bearer(bob["access_token"])).json()
    assert [c["id"] for c in chats["conversations"]] == [bob_chat["id"]]
    assert auth_client.get(
        f"/conversations/{bob_chat['id']}", headers=_bearer(bob["access_token"])
    ).status_code == 200
