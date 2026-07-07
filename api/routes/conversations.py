"""Saved-chat endpoints. All require auth; a user only ever sees and mutates
their own conversations (ownership checked on every id)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import allowed_tenants, current_principal
from core.security.auth import Principal, question_hash

router = APIRouter(tags=["conversations"])


class NewChat(BaseModel):
    title: str = Field(default="New chat", max_length=120)


class Rename(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class Ask(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    industry: str | None = Field(default=None, max_length=80)


def _store(request: Request):
    store = request.app.state.conversations
    if store is None:
        raise HTTPException(status_code=404, detail="chat history requires auth")
    return store


@router.post("/conversations")
def create(body: NewChat, request: Request, p: Principal = Depends(current_principal)):
    return _store(request).create(p.username, body.title)


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


@router.post("/conversations/{conv_id}/messages")
def ask(conv_id: str, body: Ask, request: Request, p: Principal = Depends(current_principal)):
    """Ask a question within a saved chat: run the pipeline, persist both the
    user turn and the cited answer, and return the answer."""
    store = _store(request)
    conv = store.get(conv_id, p.username)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    tenants = allowed_tenants(request, p)
    result = request.app.state.pipeline.query(
        body.question, industry=body.industry, tenants=tenants
    ).to_dict()

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
