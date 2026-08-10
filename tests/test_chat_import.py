"""Carrying an anonymous thread into the account that just signed in.

The login wall fires *because* the visitor ran out of free questions, so before
`POST /conversations/import` existed the act of signing up destroyed the exact
conversation that prompted it: the client cleared `anonMsgs` and nothing had
stored them.

The interesting cases are not "does it round-trip". They are:
  - citations must survive, or a citation-first product hands the user a
    transcript whose answers look uncited,
  - it must not spend a free question and must never reach a model, which is
    also what stops it being a way to get answers for free,
  - and it must land only in the caller's own account.
"""

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(settings, monkeypatch):
    monkeypatch.setenv("EURAG_AUTH_ENABLED", "true")
    monkeypatch.setenv("EURAG_JWT_SECRET", "test-secret-at-least-32-bytes-long!!")
    monkeypatch.setenv("EURAG_ENCRYPTION_KEY", os.urandom(32).hex())
    from api.main import app

    with TestClient(app) as c:
        yield c


def _bearer(t):
    return {"Authorization": f"Bearer {t}"}


def _login(client, username="alice"):
    client.post("/auth/register", json={"username": username, "password": "longpassword1"})
    r = client.post("/auth/login", json={"username": username, "password": "longpassword1"})
    return _bearer(r.json()["access_token"])


# --- carrying an anonymous thread into a new account ------------------------
# The login wall fires *because* the free questions ran out, so sign-up used to
# destroy the exact conversation that prompted it (`setAnonMsgs([])` before
# anything had stored it). POST /conversations/import adopts it verbatim.

ANON_THREAD = [
    {"role": "user", "content": "Do I need a DPO for 30 people?", "citations": [], "meta": {}},
    {
        "role": "assistant",
        "content": "Not automatically [1].",
        "citations": [
            {
                "marker": 1,
                "title": "Regulation (EU) 2016/679",
                "source_url": "https://eur-lex.europa.eu/x",
                "quote": "The controller shall designate...",
                "chunk_id": "c1",
            }
        ],
        "meta": {"mode": "llm", "escalated": False, "insufficient": False},
    },
]


def test_importing_an_anonymous_thread_preserves_turns_and_citations(client):
    """A citation-first product that drops citations on import would leave the
    user looking at an answer that appears uncited."""
    auth = _login(client)
    r = client.post("/conversations/import", json={"messages": ANON_THREAD}, headers=auth)
    assert r.status_code == 200
    conv = r.json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
    assert conv["messages"][1]["citations"][0]["title"] == "Regulation (EU) 2016/679"
    assert conv["messages"][1]["meta"]["mode"] == "llm"
    # titled from the first question, like a chat that was asked normally
    assert conv["title"].startswith("Do I need a DPO")
    # and it shows up in the sidebar
    assert any(c["id"] == conv["id"] for c in client.get("/conversations", headers=auth).json()["conversations"])


def test_importing_does_not_spend_a_free_question(client, monkeypatch):
    """These answers were already paid for on the anonymous tier. Charging again
    for text the user is already looking at would be wrong — and it is also what
    stops this route being a way to get answers for free."""
    monkeypatch.setenv("EURAG_FREE_USER_QUESTIONS", "10")
    auth = _login(client)
    before = client.get("/account", headers=auth).json()["free_remaining"]
    client.post("/conversations/import", json={"messages": ANON_THREAD}, headers=auth)
    assert client.get("/account", headers=auth).json()["free_remaining"] == before


def test_import_never_reaches_the_model(client, monkeypatch):
    """The strongest form of the above: no pipeline call at all."""
    calls = []
    original = client.app.state.pipeline.query
    client.app.state.pipeline.query = lambda *a, **k: calls.append(1) or original(*a, **k)
    auth = _login(client)
    client.post("/conversations/import", json={"messages": ANON_THREAD}, headers=auth)
    assert calls == []


def test_import_requires_auth(client):
    assert client.post("/conversations/import", json={"messages": ANON_THREAD}).status_code == 401


def test_import_lands_only_in_the_callers_own_account(client):
    auth_a = _login(client, "alice")
    conv = client.post("/conversations/import", json={"messages": ANON_THREAD}, headers=auth_a).json()
    auth_b = _login(client, "bobbington")
    assert client.get(f"/conversations/{conv['id']}", headers=auth_b).status_code == 404
    assert client.get("/conversations", headers=auth_b).json()["conversations"] == []


@pytest.mark.parametrize(
    "bad",
    [
        {"messages": []},                                             # nothing to adopt
        {"messages": [{"role": "system", "content": "x"}]},           # role not in the pair
        {"messages": [{"role": "user", "content": "x" * 20001}]},     # unbounded content
        {"messages": [{"role": "user", "content": "x"}] * 41},        # unbounded length
    ],
)
def test_import_is_bounded(client, bad):
    auth = _login(client)
    assert client.post("/conversations/import", json=bad, headers=auth).status_code == 422
