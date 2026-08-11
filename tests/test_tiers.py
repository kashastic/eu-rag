"""Access tiers: anonymous free-question gate, login wall, BYOK unlock."""

import os
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from core.db import Database
from core.quota import AnonQuota, UserQuota


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


def test_a_greeting_does_not_spend_an_anonymous_question(client):
    """No model call, no charge.

    The anonymous allowance is two questions, and it exists to bound what a
    public URL can spend on the owner's Anthropic key. A greeting spends
    nothing — it is answered from a constant — so charging for it would trade
    the random-answer bug for a shorter free trial.
    """
    r = client.post("/query", json={"question": "hello"})

    assert r.status_code == 200
    assert r.json()["mode"] == "smalltalk"
    assert r.json()["anon_remaining"] == 3  # untouched

    # ...and the allowance is genuinely still there, not merely reported
    for _ in range(3):
        assert client.post("/query", json={"question": "SME thresholds?"}).status_code == 200
    assert client.post("/query", json={"question": "SME thresholds?"}).status_code == 401


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


# --- the logged-in free tier's LIFETIME allowance --------------------------

def test_user_quota_consumes_then_blocks_and_never_resets(tmp_path):
    q = UserQuota(Database(None, sqlite_path=tmp_path / "q.db"))
    assert q.consume("alice", 3) == (True, 2)
    assert q.consume("alice", 3) == (True, 1)
    assert q.consume("alice", 3) == (True, 0)
    assert q.consume("alice", 3) == (False, 0)
    # no day column to roll over: a second instance on the same DB agrees
    assert UserQuota(Database(None, sqlite_path=tmp_path / "q.db")).remaining("alice", 3) == 0
    assert q.consume("bob", 3) == (True, 2)  # per user, not global


def test_user_quota_refund_cannot_go_negative(tmp_path):
    q = UserQuota(Database(None, sqlite_path=tmp_path / "q.db"))
    q.refund("alice")  # nothing consumed yet
    assert q.remaining("alice", 3) == 3
    q.consume("alice", 3)
    q.refund("alice")
    q.refund("alice")  # second refund has nothing to give back
    assert q.remaining("alice", 3) == 3


@pytest.fixture()
def free_client(settings, monkeypatch):
    """Auth on, a two-question free allowance so the wall is cheap to reach."""
    monkeypatch.setenv("EURAG_AUTH_ENABLED", "true")
    monkeypatch.setenv("EURAG_JWT_SECRET", "test-secret-at-least-32-bytes-long!!")
    monkeypatch.setenv("EURAG_FREE_ANON_QUESTIONS", "0")
    monkeypatch.setenv("EURAG_FREE_USER_QUESTIONS", "2")
    monkeypatch.setenv("EURAG_ENCRYPTION_KEY", os.urandom(32).hex())
    from api.main import app

    with TestClient(app) as c:
        yield c


def _account(client, name="carol"):
    client.post("/auth/register", json={"username": name, "password": "longpassword1"})
    return client.post(
        "/auth/login", json={"username": name, "password": "longpassword1"}
    ).json()["access_token"]


def test_free_user_gets_the_allowance_then_402(free_client):
    tok = _account(free_client)
    for i in range(2):
        r = free_client.post("/query", json={"question": "SME thresholds?"}, headers=_bearer(tok))
        assert r.status_code == 200
        assert r.json()["tier"] == "free"
        assert r.json()["free_remaining"] == 1 - i
    walled = free_client.post(
        "/query", json={"question": "one more?"}, headers=_bearer(tok)
    )
    # 402, never 401 — the web client treats 401 as "refresh the token"
    assert walled.status_code == 402
    assert walled.json()["detail"]["code"] == "free_limit_reached"


def test_the_saved_chat_route_shares_the_same_allowance(free_client):
    """Two logged-in ask paths, one gate: spending via /query must leave the
    conversation route with less, or the limit is bypassable by switching UI."""
    tok = _account(free_client, "dave")
    conv = free_client.post("/conversations", json={}, headers=_bearer(tok)).json()
    r = free_client.post(
        f"/conversations/{conv['id']}/messages",
        json={"question": "SME thresholds?"}, headers=_bearer(tok),
    )
    assert r.status_code == 200 and r.json()["free_remaining"] == 1
    free_client.post("/query", json={"question": "and again?"}, headers=_bearer(tok))
    walled = free_client.post(
        f"/conversations/{conv['id']}/messages",
        json={"question": "third one"}, headers=_bearer(tok),
    )
    assert walled.status_code == 402
    assert walled.json()["detail"]["code"] == "free_limit_reached"


def test_a_greeting_does_not_spend_a_free_users_allowance_on_either_path(free_client):
    """Both logged-in ask paths, same rule.

    The free allowance is for the lifetime of the account, so a question spent
    on "hello" is gone for good. And a refund present on one of the two doors
    is the same class of bug as a gate present on one of them.
    """
    tok = _account(free_client, "grace")
    conv = free_client.post("/conversations", json={}, headers=_bearer(tok)).json()

    direct = free_client.post("/query", json={"question": "hello"}, headers=_bearer(tok))
    assert direct.json()["mode"] == "smalltalk"
    assert direct.json()["free_remaining"] == 2  # of 2

    saved = free_client.post(
        f"/conversations/{conv['id']}/messages",
        json={"question": "thanks"}, headers=_bearer(tok),
    )
    assert saved.json()["mode"] == "smalltalk"
    assert saved.json()["free_remaining"] == 2

    # the account agrees — the refund hit the store, not just the response
    assert free_client.get("/account", headers=_bearer(tok)).json()["free_remaining"] == 2
    # and a real question still costs one
    free_client.post("/query", json={"question": "SME thresholds?"}, headers=_bearer(tok))
    assert free_client.get("/account", headers=_bearer(tok)).json()["free_remaining"] == 1


def test_byok_bypasses_the_allowance_and_does_not_spend_it(free_client):
    tok = _account(free_client, "erin")
    free_client.post("/query", json={"question": "SME thresholds?"}, headers=_bearer(tok))
    free_client.put(
        "/account/api-key",
        json={"api_key": "sk-ant-test-key-000000000000000000"},
        headers=_bearer(tok),
    )
    # BYOK is billed to the user, so the server stops counting entirely
    for _ in range(4):
        r = free_client.post("/query", json={"question": "SME thresholds?"}, headers=_bearer(tok))
        assert r.status_code == 200 and r.json()["tier"] == "byok"
        assert "free_remaining" not in r.json()
    # removing the key returns the untouched remainder of the free allowance
    free_client.delete("/account/api-key", headers=_bearer(tok))
    acct = free_client.get("/account", headers=_bearer(tok)).json()
    assert acct["tier"] == "free" and acct["free_remaining"] == 1


def test_account_reports_the_allowance_and_key_age_but_never_the_key(free_client):
    tok = _account(free_client, "frank")
    acct = free_client.get("/account", headers=_bearer(tok)).json()
    assert acct["free_limit"] == 2 and acct["free_remaining"] == 2
    assert acct["api_key_set_at"] is None
    free_client.put(
        "/account/api-key",
        json={"api_key": "sk-ant-test-key-000000000000000000"},
        headers=_bearer(tok),
    )
    acct = free_client.get("/account", headers=_bearer(tok)).json()
    assert acct["api_key_set_at"] > 0  # drives the rotation nudge
    assert "sk-ant" not in str(acct)
