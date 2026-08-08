"""Access tiers: anonymous free-question gate, login wall, BYOK unlock."""

import os
from datetime import date, timedelta

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


def test_anon_quota_refund_restores_a_question(tmp_path):
    q = AnonQuota(Database(None, sqlite_path=tmp_path / "q.db"))
    q.consume("ip:x", 3)
    q.consume("ip:x", 3)
    q.refund("ip:x")  # one of the two calls failed after consuming
    assert q.remaining("ip:x", 3) == 2


def test_anon_quota_refund_at_zero_is_a_noop(tmp_path):
    q = AnonQuota(Database(None, sqlite_path=tmp_path / "q.db"))
    q.refund("ip:x")  # nothing consumed today — must not go negative
    assert q.remaining("ip:x", 3) == 3
    q.consume("ip:x", 3)
    q.refund("ip:x")
    q.refund("ip:x")  # second refund has nothing to give back
    assert q.remaining("ip:x", 3) == 3


def test_anon_quota_sweeps_stale_days(tmp_path):
    """The table grows a row per IP per day; consume prunes what's expired."""
    db = Database(None, sqlite_path=tmp_path / "q.db")
    q = AnonQuota(db)
    today = date.today()
    days = {
        "stale": (today - timedelta(days=9)).isoformat(),
        "edge": (today - timedelta(days=2)).isoformat(),  # retained
        "yesterday": (today - timedelta(days=1)).isoformat(),
    }
    with db.transaction() as tx:
        for name, day in days.items():
            tx.execute(
                "INSERT INTO anon_quota (quota_key, day, used) VALUES (?, ?, 1)",
                (f"ip:{name}", day),
            )

    q.consume("ip:today", 3)  # first-of-day insert triggers the sweep

    kept = {r["day"] for r in db.query("SELECT day FROM anon_quota")}
    assert days["stale"] not in kept
    assert kept == {days["edge"], days["yesterday"], today.isoformat()}


def test_anon_quota_sweep_leaves_todays_counts_alone(tmp_path):
    db = Database(None, sqlite_path=tmp_path / "q.db")
    q = AnonQuota(db)
    q.consume("ip:a", 3)
    q.consume("ip:a", 3)
    q.consume("ip:b", 3)  # a new key's insert sweeps — must not clear ip:a
    assert q.remaining("ip:a", 3) == 1


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


def test_anon_quota_key_ignores_forwarded_for_by_default(client):
    """The free-question allowance is per IP — a spoofable header must not be
    able to hand out fresh allowances when we aren't behind a proxy."""
    for hop in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
        r = client.post(
            "/query", json={"question": "SME thresholds?"},
            headers={"X-Forwarded-For": hop},
        )
        assert r.status_code == 200  # same bucket, counting down
    walled = client.post(
        "/query", json={"question": "SME thresholds?"},
        headers={"X-Forwarded-For": "4.4.4.4"},
    )
    assert walled.status_code == 401


def test_anon_quota_key_uses_forwarded_for_when_trusted(settings, monkeypatch):
    monkeypatch.setenv("EURAG_AUTH_ENABLED", "true")
    monkeypatch.setenv("EURAG_JWT_SECRET", "test-secret-at-least-32-bytes-long!!")
    monkeypatch.setenv("EURAG_FREE_ANON_QUESTIONS", "1")
    monkeypatch.setenv("EURAG_TRUST_PROXY", "true")
    from api.main import app

    with TestClient(app) as c:
        ask = lambda hop: c.post(  # noqa: E731
            "/query", json={"question": "SME thresholds?"},
            headers={"X-Forwarded-For": hop},
        ).status_code
        assert ask("1.1.1.1") == 200
        assert ask("1.1.1.1") == 401   # that client's single question is spent
        assert ask("2.2.2.2") == 200   # a different client still has its own


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


# (per-IP independence of the allowance is asserted above, in the two
# EURAG_TRUST_PROXY tests — X-Forwarded-For only identifies a client when the
# deployment says the header is trustworthy.)
