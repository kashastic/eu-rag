"""Access tiers: anonymous free-question gate, login wall, BYOK unlock."""

import os

import pytest
from fastapi.testclient import TestClient

from core.db import Database
from core.quota import AnonQuota


# --- unit: the server-side quota (the real cost gate) ----------------------

def test_anon_quota_consumes_then_blocks(tmp_path):
    q = AnonQuota(Database(None, sqlite_path=tmp_path / "q.db"))
    assert q.consume("ip:1.2.3.4", 3) == (True, 2)
    assert q.consume("ip:1.2.3.4", 3) == (True, 1)
    assert q.consume("ip:1.2.3.4", 3) == (True, 0)
    assert q.consume("ip:1.2.3.4", 3) == (False, 0)  # spent
    # a different IP has its own allowance
    assert q.consume("ip:5.6.7.8", 3) == (True, 2)


def test_anon_quota_remaining_readonly(tmp_path):
    q = AnonQuota(Database(None, sqlite_path=tmp_path / "q.db"))
    assert q.remaining("ip:x", 3) == 3
    q.consume("ip:x", 3)
    assert q.remaining("ip:x", 3) == 2


# --- API: the full anonymous → wall → login → BYOK flow --------------------

@pytest.fixture()
def client(settings, monkeypatch):
    monkeypatch.setenv("EURAG_AUTH_ENABLED", "true")
    monkeypatch.setenv("EURAG_JWT_SECRET", "test-secret-at-least-32-bytes-long!!")
    monkeypatch.setenv("EURAG_FREE_ANON_QUESTIONS", "3")
    monkeypatch.setenv("EURAG_ENCRYPTION_KEY", os.urandom(32).hex())
    from api.main import app

    with TestClient(app) as c:
        yield c


def _bearer(t):
    return {"Authorization": f"Bearer {t}"}


def test_anonymous_gets_three_then_login_wall(client):
    for i in range(3):
        r = client.post("/query", json={"question": "What is an SME under EU rules?"})
        assert r.status_code == 200
        assert r.json()["tier"] == "anonymous"
        assert r.json()["anon_remaining"] == 2 - i
    # fourth is walled
    walled = client.post("/query", json={"question": "one more please?"})
    assert walled.status_code == 401
    assert walled.json()["detail"]["code"] == "anonymous_limit_reached"


def test_logged_in_free_tier_reports_free(client):
    client.post("/auth/register", json={"username": "alice", "password": "longpassword1"})
    tok = client.post(
        "/auth/login", json={"username": "alice", "password": "longpassword1"}
    ).json()["access_token"]
    r = client.post("/query", json={"question": "SME thresholds?"}, headers=_bearer(tok))
    assert r.status_code == 200
    assert r.json()["tier"] == "free"


def test_byok_set_status_and_unlock(client):
    client.post("/auth/register", json={"username": "bob", "password": "longpassword1"})
    tok = client.post(
        "/auth/login", json={"username": "bob", "password": "longpassword1"}
    ).json()["access_token"]

    assert client.get("/account", headers=_bearer(tok)).json()["tier"] == "free"
    # bad key rejected
    assert client.put(
        "/account/api-key", json={"api_key": "not-a-key-xxxxxxxxxxxxxxxx"}, headers=_bearer(tok)
    ).status_code == 422
    # valid-looking key accepted and stored
    set_res = client.put(
        "/account/api-key",
        json={"api_key": "sk-ant-test-key-000000000000000000"},
        headers=_bearer(tok),
    )
    assert set_res.status_code == 200
    acct = client.get("/account", headers=_bearer(tok)).json()
    assert acct["tier"] == "byok" and acct["has_api_key"] is True
    # the raw key is never returned anywhere
    assert "sk-ant" not in str(acct)
    # a query now runs on the byok tier
    q = client.post("/query", json={"question": "SME thresholds?"}, headers=_bearer(tok))
    assert q.json()["tier"] == "byok"
    # clearing reverts to free
    client.delete("/account/api-key", headers=_bearer(tok))
    assert client.get("/account", headers=_bearer(tok)).json()["tier"] == "free"


def test_anonymous_quota_is_per_ip_not_shared(client):
    # different X-Forwarded-For clients get independent allowances
    for ip in ("1.1.1.1", "2.2.2.2"):
        r = client.post(
            "/query",
            json={"question": "What is an SME?"},
            headers={"X-Forwarded-For": ip},
        )
        assert r.status_code == 200 and r.json()["anon_remaining"] == 2
