"""Conversation store (saved chats) — on SQLite. Postgres parity is covered
by tests/test_postgres.py when a database is available."""

import pytest

from core.conversations import ConversationStore
from core.db import Database


@pytest.fixture()
def store(tmp_path):
    db = Database(None, sqlite_path=tmp_path / "eurag.db")
    yield ConversationStore(db)
    db.close()


def test_create_list_and_get(store):
    conv = store.create("alice", "Late payments")
    assert conv["id"].startswith("conv_")
    listed = store.list("alice")
    assert [c["id"] for c in listed] == [conv["id"]]
    full = store.get(conv["id"], "alice")
    assert full["title"] == "Late payments"
    assert full["messages"] == []


def test_messages_persist_with_citations(store):
    conv = store.create("alice")
    store.add_message(conv["id"], "user", "What interest can I charge?")
    store.add_message(
        conv["id"], "assistant", "Statutory interest applies [1].",
        citations=[{"marker": 1, "title": "Late Payment Directive"}],
        meta={"mode": "llm", "escalated": False},
    )
    msgs = store.get(conv["id"], "alice")["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["citations"][0]["title"] == "Late Payment Directive"
    assert msgs[1]["meta"]["mode"] == "llm"


def test_users_only_see_their_own_chats(store):
    a = store.create("alice", "alice chat")
    store.create("bob", "bob chat")
    assert [c["title"] for c in store.list("alice")] == ["alice chat"]
    # bob cannot open alice's conversation by id
    assert store.get(a["id"], "bob") is None


def test_rename_and_delete_are_ownership_scoped(store):
    conv = store.create("alice", "old")
    assert store.rename(conv["id"], "bob", "hijack") is False  # not bob's
    assert store.rename(conv["id"], "alice", "new") is True
    assert store.get(conv["id"], "alice")["title"] == "new"
    assert store.delete(conv["id"], "bob") is False
    assert store.delete(conv["id"], "alice") is True
    assert store.get(conv["id"], "alice") is None


def test_updated_at_advances_on_new_message(store):
    conv = store.create("alice")
    before = store.list("alice")[0]["updated_at"]
    store.add_message(conv["id"], "user", "hi")
    after = store.list("alice")[0]["updated_at"]
    assert after >= before


def test_erase_user_clears_all_chats(store):
    store.create("alice", "one")
    c2 = store.create("alice", "two")
    store.add_message(c2["id"], "user", "hi")
    store.create("bob", "keep")
    assert store.erase_user("alice") == 2
    assert store.list("alice") == []
    assert len(store.list("bob")) == 1
