"""Follow-up questions in an anonymous thread.

`/query` is stateless, so the client sends prior turns with each follow-up. That
made the request grow with the conversation, and the caps on those turns used to
be `max_length` — which **rejects**. A third question in a thread whose second
answer ran long came back as
`422 answer: String should have at most 2000 characters`, rendered in the
transcript where the answer should have been.

Two things made that especially bad:
  - request validation runs before the route body, so it failed ahead of the
    quota check and ahead of the bot gate,
  - and the over-long text was EURAG's *own previous answer*, so the user was
    refused for something they did not write.

Prior turns are now truncated instead. Nothing is lost: the only consumer is
`QueryContextualizer.standalone`, which reads at most MAX_ANSWER_CHARS of an
answer and only the last MAX_TURNS turns.
"""

import os

import pytest
from fastapi.testclient import TestClient

from api.routes.query import HISTORY_CHARS, HistoryTurn
from core.retrieval.expansion import QueryContextualizer


@pytest.fixture()
def client(settings, monkeypatch):
    monkeypatch.setenv("EURAG_AUTH_ENABLED", "true")
    monkeypatch.setenv("EURAG_JWT_SECRET", "test-secret-at-least-32-bytes-long!!")
    monkeypatch.setenv("EURAG_FREE_ANON_QUESTIONS", "20")
    from api.main import app

    with TestClient(app) as c:
        yield c


LONG_ANSWER = "Article 37(1) makes a DPO mandatory in three cases. " * 120  # ~6000 chars


def test_a_long_prior_answer_no_longer_breaks_the_follow_up(client):
    """The reported bug, end to end: two turns behind you, the second long."""
    assert len(LONG_ANSWER) > HISTORY_CHARS
    r = client.post(
        "/query",
        json={
            "question": "which one would you recommend?",
            "history": [
                {"question": "Do I need a DPO?", "answer": "Short answer."},
                {"question": "What about record-keeping?", "answer": LONG_ANSWER},
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert "answer" in r.json()


def test_prior_turns_are_truncated_not_rejected():
    turn = HistoryTurn(question="q" * 5000, answer="a" * 5000)
    assert len(turn.question) == HISTORY_CHARS
    assert len(turn.answer) == HISTORY_CHARS


def test_truncation_keeps_the_beginning_which_is_what_the_rewrite_reads():
    """The contextualiser reads the *start* of an answer, so slicing from the
    front is what makes truncation lossless here."""
    turn = HistoryTurn(question="q", answer="TOPIC SENTENCE. " + "x" * 9000)
    assert turn.answer.startswith("TOPIC SENTENCE.")
    assert len(turn.answer) == HISTORY_CHARS


def test_the_cap_is_generous_next_to_what_is_actually_consumed():
    """If this ever inverts, truncation would start losing information the
    rewrite depends on."""
    assert HISTORY_CHARS > QueryContextualizer.MAX_ANSWER_CHARS


def test_a_thread_can_run_past_the_third_question(client):
    """Walk the exact shape the web client sends, several turns deep."""
    history = []
    for i in range(5):
        r = client.post(
            "/query",
            json={"question": f"Follow-up number {i}, what applies?", "history": history[-3:]},
        )
        assert r.status_code == 200, f"turn {i}: {r.text}"
        history.append({"question": f"Follow-up number {i}, what applies?", "answer": LONG_ANSWER})


def test_history_is_still_bounded(client):
    """Truncating per turn must not mean an unbounded number of turns."""
    r = client.post(
        "/query",
        json={
            "question": "and now?",
            "history": [{"question": "q", "answer": "a"} for _ in range(11)],
        },
    )
    assert r.status_code == 422
