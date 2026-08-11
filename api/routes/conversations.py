"""Saved-chat endpoints. All require auth; a user only ever sees and mutates
their own conversations (ownership checked on every id)."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import (
    allowed_tenants,
    cost_nothing,
    current_principal,
    paid_tier,
    refund_free_question,
    spend_free_question,
)
from api.routes.query import ProfileBody, profile_of
from core.generation.llm_client import LLMUnavailableError
from core.security.auth import Principal, question_hash

router = APIRouter(tags=["conversations"])


class NewChat(BaseModel):
    title: str = Field(default="New chat", max_length=120)


class Rename(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class Ask(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    # the profile must reach BOTH logged-in ask paths — this one and /query —
    # or the web app's saved chats silently lose the tailoring that the
    # anonymous thread has
    profile: ProfileBody | None = None
    # deprecated alongside QueryRequest.industry; accepted, never forwarded
    industry: str | None = Field(default=None, max_length=80, deprecated=True)


class ImportedCitation(BaseModel):
    """One citation as the client already received it. Capped like every other
    client-supplied string that reaches storage."""

    marker: int = Field(ge=1, le=99)
    title: str = Field(default="", max_length=300)
    source_url: str = Field(default="", max_length=1000)
    quote: str = Field(default="", max_length=4000)
    chunk_id: str = Field(default="", max_length=200)


class ImportedMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20000)
    citations: list[ImportedCitation] = Field(default_factory=list, max_length=12)
    meta: dict = Field(default_factory=dict)


class ImportChat(BaseModel):
    # An anonymous visitor gets a handful of free questions, so a real thread is
    # a few turns; the cap is generous and exists to bound the write, not to
    # shape the feature.
    messages: list[ImportedMessage] = Field(min_length=1, max_length=40)


def _store(request: Request):
    store = request.app.state.conversations
    if store is None:
        raise HTTPException(status_code=404, detail="chat history requires auth")
    return store


@router.post("/conversations")
def create(body: NewChat, request: Request, p: Principal = Depends(current_principal)):
    return _store(request).create(p.username, body.title)


@router.post("/conversations/import")
def import_chat(body: ImportChat, request: Request, p: Principal = Depends(current_principal)):
    """Adopt an anonymous thread into the account that just signed in.

    The login wall fires precisely *because* someone ran out of free questions,
    so without this the act of signing up destroys the conversation that
    prompted it. The turns are stored verbatim.

    **No pipeline call and no quota spend.** These answers were already produced
    and already paid for on the anonymous tier; re-running them would charge the
    user (or the server) twice for text they are looking at. That is also why
    this route cannot be used to get a free answer — it never reaches a model.

    The content is client-supplied, but it lands only in the caller's own
    private conversation, which they could fill with anything by typing. It is
    capped and shape-checked so it cannot be used as unbounded storage.
    """
    store = _store(request)
    first_question = next(
        (m.content for m in body.messages if m.role == "user" and m.content.strip()), ""
    )
    conv = store.create(p.username, first_question[:60] or "New chat")
    for m in body.messages:
        store.add_message(
            conv["id"],
            m.role,
            m.content,
            citations=[c.model_dump() for c in m.citations],
            meta=m.meta,
        )
    if request.app.state.auth_enabled:
        # a new way for content to enter a tenant belongs in the trail
        request.app.state.auth.audit(
            p.username,
            "conversation.import",
            resource=conv["id"],
            detail=f"{len(body.messages)} messages",
        )
    return store.get(conv["id"], p.username)


@router.get("/conversations")
def list_chats(request: Request, p: Principal = Depends(current_principal)):
    return {"conversations": _store(request).list(p.username)}


@router.get("/conversations/{conv_id}")
def get_chat(conv_id: str, request: Request, p: Principal = Depends(current_principal)):
    conv = _store(request).get(conv_id, p.username)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv


@router.patch("/conversations/{conv_id}")
def rename(conv_id: str, body: Rename, request: Request, p: Principal = Depends(current_principal)):
    if not _store(request).rename(conv_id, p.username, body.title):
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"id": conv_id, "title": body.title}


@router.delete("/conversations/{conv_id}")
def delete(conv_id: str, request: Request, p: Principal = Depends(current_principal)):
    if not _store(request).delete(conv_id, p.username):
        raise HTTPException(status_code=404, detail="conversation not found")
    if request.app.state.auth_enabled:
        request.app.state.auth.audit(p.username, "conversation.delete", resource=conv_id)
    return {"deleted": conv_id}


def _history(conv: dict) -> list[tuple[str, str]]:
    """Prior (question, answer) turns from the stored conversation, oldest
    first, so a follow-up can be rewritten to stand on its own before
    retrieval. Unlike /query — which is stateless and must take the client's
    word for it — this route already holds the persisted chat, so the history
    is server-owned and needs no caps or trust. Messages alternate user then
    assistant; a user turn whose answer failed simply carries an empty one."""
    turns: list[tuple[str, str]] = []
    messages = conv.get("messages") or []
    for i, msg in enumerate(messages):
        if msg.get("role") != "user":
            continue
        nxt = messages[i + 1] if i + 1 < len(messages) else None
        answer = nxt.get("content", "") if nxt and nxt.get("role") == "assistant" else ""
        turns.append((msg.get("content", ""), answer))
    return turns


@router.post("/conversations/{conv_id}/messages")
def ask(conv_id: str, body: Ask, request: Request, p: Principal = Depends(current_principal)):
    """Ask a question within a saved chat: run the pipeline, persist both the
    user turn and the cited answer, and return the answer."""
    store = _store(request)
    conv = store.get(conv_id, p.username)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    tenants = allowed_tenants(request, p)
    plan = paid_tier(request, p)
    # the free tier's lifetime allowance — same gate as /query, one helper, so
    # the two logged-in ask paths can't drift apart
    free_remaining = spend_free_question(request, p, plan)
    try:
        result = request.app.state.pipeline.query(
            body.question,
            profile=profile_of(body),
            tenants=tenants,
            answer_model=plan["answer_model"],
            escalation_model=plan["escalation_model"],
            api_key=plan["api_key"],
            history=_history(conv),
        ).to_dict()
    except LLMUnavailableError:
        refund_free_question(request, p, plan)
        raise
    # no model call, no charge — same rule as the anonymous path, so "hello"
    # costs a free-tier user nothing whichever door they came in through
    if cost_nothing(result):
        refund_free_question(request, p, plan)
        if free_remaining is not None:
            free_remaining += 1
    result["tier"] = plan["tier"]
    if free_remaining is not None:
        result["free_remaining"] = free_remaining

    store.add_message(conv_id, "user", body.question)
    store.add_message(
        conv_id,
        "assistant",
        result["answer"],
        citations=result["citations"],
        meta={
            "mode": result["mode"],
            "escalated": result["escalated"],
            "insufficient": result["insufficient"],
        },
    )
    # title an untitled chat from its first question
    if conv["title"] in ("New chat", "") and len(conv["messages"]) == 0:
        store.rename(conv_id, p.username, body.question[:60])

    if request.app.state.auth_enabled:
        request.app.state.auth.audit(
            p.username, "conversation.ask", resource=conv_id, detail=question_hash(body.question)
        )
    return result
