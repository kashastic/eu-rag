"""Postgres parity for the shared multi-instance stores.

Opt-in: set EURAG_TEST_DATABASE_URL to a throwaway Postgres, e.g.
    docker run -d --name pg -e POSTGRES_PASSWORD=eurag -e POSTGRES_DB=eurag \\
        -p 55432:5432 postgres:16-alpine
    EURAG_TEST_DATABASE_URL=postgresql://postgres:eurag@localhost:55432/eurag \\
        pytest tests/test_postgres.py
Skips (never fails CI) when the variable is unset. Each test uses a unique
schema-ish username prefix so reruns against the same DB don't collide.
"""

import os
import uuid

import pytest

from core.conversations import ConversationStore
from core.db import Database
from core.security.auth import AuthError, AuthStore

URL = os.environ.get("EURAG_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not URL, reason="set EURAG_TEST_DATABASE_URL to run Postgres parity tests"
)


@pytest.fixture()
def db():
    d = Database(URL)
    yield d
    d.close()


def _u(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def test_auth_on_postgres(db):
    auth = AuthStore(db, "x" * 40)
    name = _u("alice")
    p = auth.register(name, "longpassword1")
    assert p.tenant == name
    tokens = auth.issue_tokens(p)
    assert auth.verify_access(tokens["access_token"]).username == name
    auth.refresh(tokens["refresh_token"])
    with pytest.raises(AuthError):
        auth.refresh(tokens["refresh_token"])  # single-use across the fleet


def test_conversations_on_postgres(db):
    conv = ConversationStore(db)
    owner = _u("bob")
    c = conv.create(owner, "chat")
    conv.add_message(c["id"], "user", "q")
    conv.add_message(
        c["id"], "assistant", "a [1].", citations=[{"marker": 1, "title": "X"}]
    )
    full = conv.get(c["id"], owner)
    assert [m["role"] for m in full["messages"]] == ["user", "assistant"]
    assert full["messages"][1]["citations"][0]["title"] == "X"
    assert conv.get(c["id"], _u("intruder")) is None  # isolation holds on PG
