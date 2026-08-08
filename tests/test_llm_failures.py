"""API behaviour when the upstream LLM fails: no raw 500s, friendly codes,
and an anonymous user never pays a free question for an unanswered call."""

import pytest
from fastapi.testclient import TestClient

from core.generation.llm_client import LLMUnavailableError


@pytest.fixture()
def client(settings, monkeypatch):
    monkeypatch.setenv("EURAG_AUTH_ENABLED", "true")
    monkeypatch.setenv("EURAG_JWT_SECRET", "test-secret-at-least-32-bytes-long!!")
    monkeypatch.setenv("EURAG_FREE_ANON_QUESTIONS", "3")
    from api.main import app

    with TestClient(app) as c:
        yield c


class _Flaky:
    """Wraps pipeline.query: raises while armed, then answers normally."""

    def __init__(self, pipeline, kind):
        self._orig = pipeline.query
        self.kind = kind
        self.armed = True

    def __call__(self, *args, **kwargs):
        if self.armed:
            raise LLMUnavailableError(self.kind)
        return self._orig(*args, **kwargs)


def test_transient_failure_is_503_and_refunds_anon_question(client, monkeypatch):
    pipeline = client.app.state.pipeline
    flaky = _Flaky(pipeline, "overloaded")
    monkeypatch.setattr(pipeline, "query", flaky)

    r = client.post("/query", json={"question": "What is an SME?"})
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "llm_unavailable"
    assert r.headers["retry-after"] == "10"

    # the failed call was refunded: a working retry still has the full
    # allowance minus only itself
    flaky.armed = False
    ok = client.post("/query", json={"question": "What is an SME?"})
    assert ok.status_code == 200
    assert ok.json()["anon_remaining"] == 2


def test_rejected_byok_key_is_a_friendly_400(client, monkeypatch):
    client.post(
        "/auth/register", json={"username": "carol", "password": "longpassword1"}
    )
    tok = client.post(
        "/auth/login", json={"username": "carol", "password": "longpassword1"}
    ).json()["access_token"]

    pipeline = client.app.state.pipeline
    monkeypatch.setattr(pipeline, "query", _Flaky(pipeline, "auth"))
    r = client.post(
        "/query",
        json={"question": "SME thresholds?"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "byok_key_rejected"
