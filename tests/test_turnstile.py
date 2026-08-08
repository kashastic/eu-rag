"""Turnstile at the API boundary: anonymous /query and /auth/register are
gated when EURAG_TURNSTILE_SECRET is set, and a rejected challenge never
burns an anonymous free question. With no secret configured the gate is off
entirely — that path is what every other API suite exercises."""

import pytest
from fastapi.testclient import TestClient

from core.security import turnstile


@pytest.fixture()
def client(settings, monkeypatch):
    monkeypatch.setenv("EURAG_AUTH_ENABLED", "true")
    monkeypatch.setenv("EURAG_JWT_SECRET", "test-secret-at-least-32-bytes-long!!")
    monkeypatch.setenv("EURAG_FREE_ANON_QUESTIONS", "3")
    monkeypatch.setenv("EURAG_TURNSTILE_SECRET", "srv-secret")
    monkeypatch.setenv("EURAG_TURNSTILE_SITEKEY", "site-key-abc")
    from api.main import app

    with TestClient(app) as c:
        yield c


def _pass(monkeypatch):
    monkeypatch.setattr(turnstile, "_post", lambda u, f, t: {"success": True})


def _fail(monkeypatch):
    monkeypatch.setattr(turnstile, "_post", lambda u, f, t: {"success": False})


def test_healthz_serves_sitekey(client):
    assert client.get("/healthz").json()["turnstile_sitekey"] == "site-key-abc"


def test_anon_query_without_token_is_403_and_quota_untouched(client, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no token → no siteverify call")

    monkeypatch.setattr(turnstile, "_post", boom)
    r = client.post("/query", json={"question": "What is an SME?"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "turnstile_failed"

    # a failed challenge must not have spent a free question
    _pass(monkeypatch)
    ok = client.post(
        "/query", json={"question": "What is an SME?", "turnstile_token": "tok"}
    )
    assert ok.status_code == 200
    assert ok.json()["tier"] == "anonymous"
    assert ok.json()["anon_remaining"] == 2  # full allowance minus only this one


def test_anon_query_with_rejected_token_is_403(client, monkeypatch):
    _fail(monkeypatch)
    r = client.post(
        "/query", json={"question": "What is an SME?", "turnstile_token": "bad"}
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "turnstile_failed"


def test_anon_query_with_valid_token_answers(client, monkeypatch):
    _pass(monkeypatch)
    r = client.post(
        "/query", json={"question": "What is an SME?", "turnstile_token": "tok"}
    )
    assert r.status_code == 200 and r.json()["tier"] == "anonymous"


def test_register_without_token_is_403(client, monkeypatch):
    _fail(monkeypatch)
    r = client.post(
        "/auth/register", json={"username": "eve", "password": "longpassword1"}
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "turnstile_failed"


def test_register_with_valid_token_succeeds_and_login_ungated(client, monkeypatch):
    _pass(monkeypatch)
    r = client.post(
        "/auth/register",
        json={"username": "alice", "password": "longpassword1", "turnstile_token": "t"},
    )
    assert r.status_code == 200

    # login is never turnstile-gated — kill the network seam to prove it
    def boom(*a, **k):
        raise AssertionError("login must not call siteverify")

    monkeypatch.setattr(turnstile, "_post", boom)
    assert (
        client.post(
            "/auth/login", json={"username": "alice", "password": "longpassword1"}
        ).status_code
        == 200
    )


def test_logged_in_query_not_gated(client, monkeypatch):
    _pass(monkeypatch)
    client.post(
        "/auth/register",
        json={"username": "bob", "password": "longpassword1", "turnstile_token": "t"},
    )
    tok = client.post(
        "/auth/login", json={"username": "bob", "password": "longpassword1"}
    ).json()["access_token"]

    def boom(*a, **k):
        raise AssertionError("authed queries must not call siteverify")

    monkeypatch.setattr(turnstile, "_post", boom)
    r = client.post(
        "/query",
        json={"question": "SME thresholds?"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200 and r.json()["tier"] == "free"
