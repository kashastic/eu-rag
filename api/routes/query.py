"""Stateless /query — the anonymous entry point.

Anonymous users get `free_anon_questions` full-quality answers (the Sonnet→Opus
cascade), counted server-side per IP/day. When that allowance is spent the
route returns 401 with code `anonymous_limit_reached`, the signal for the
frontend to raise its login wall. Logged-in users are tiered by BYOK:
own-key = full cascade billed to them; free = cheap model, no escalation.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import (
    allowed_tenants,
    client_ip,
    optional_principal,
    paid_tier,
    refund_free_question,
    spend_free_question,
)
from core.generation.llm_client import LLMUnavailableError
from core.security import turnstile
from core.security.auth import Principal, question_hash

router = APIRouter(tags=["query"])


class HistoryTurn(BaseModel):
    """One prior exchange. Caps are deliberate: this is client-supplied text
    that ends up inside a prompt, so it is bounded the same way /ingest is.
    Answers are trimmed hard because only their topic matters to the rewrite."""

    question: str = Field(max_length=2000)
    answer: str = Field(default="", max_length=2000)


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    industry: str | None = Field(default=None, max_length=80)
    turnstile_token: str | None = Field(default=None, max_length=2048)
    # Prior turns, oldest first, so a follow-up can be rewritten to stand on
    # its own before retrieval. Sent by the client rather than loaded
    # server-side because anonymous users have no saved conversation — and
    # anonymous is the default demo path. max_length bounds the request; the
    # contextualizer additionally uses only the last few turns.
    history: list[HistoryTurn] = Field(default_factory=list, max_length=10)


def _history(body: QueryRequest) -> list[tuple[str, str]]:
    """Wire form -> pipeline form. Turns without a question carry no context
    to resolve against, so they are dropped rather than padded into the
    prompt."""
    return [(t.question, t.answer) for t in body.history if t.question.strip()]


@router.post("/query")
def query(
    body: QueryRequest,
    request: Request,
    principal: Principal | None = Depends(optional_principal),
):
    app = request.app
    settings = app.state.settings
    pipeline = app.state.pipeline

    # anonymous (only when auth is enabled — local mode has no gating)
    if principal is None:
        ip = client_ip(request)
        # bot gate BEFORE the quota spend — a rejected token must not burn
        # a free question. Off when no secret is configured (local default).
        if settings.turnstile_secret and not turnstile.verify(
            body.turnstile_token, settings.turnstile_secret, remoteip=ip
        ):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "turnstile_failed",
                    "message": "Verification failed — please retry the challenge.",
                },
            )
        key = "ip:" + ip
        allowed, remaining = app.state.anon_quota.consume(key, settings.free_anon_questions)
        if not allowed:
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "anonymous_limit_reached",
                    "message": "You've used your free questions. Log in to keep going.",
                },
            )
        try:
            result = pipeline.query(
                body.question,
                industry=body.industry,
                tenants=["public"],
                answer_model=settings.llm_model,
                escalation_model=settings.escalation_model,
                history=_history(body),
            ).to_dict()
        except LLMUnavailableError:
            # the question was consumed up front (parallel-overrun safety) but
            # never answered — give it back before the error handler responds
            app.state.anon_quota.refund(key)
            raise
        result["tier"] = "anonymous"
        result["anon_remaining"] = remaining
        return result

    # logged in — tier by BYOK, and the free tier has a lifetime allowance
    plan = paid_tier(request, principal)
    free_remaining = spend_free_question(request, principal, plan)
    try:
        result = pipeline.query(
            body.question,
            industry=body.industry,
            tenants=allowed_tenants(request, principal),
            answer_model=plan["answer_model"],
            escalation_model=plan["escalation_model"],
            api_key=plan["api_key"],
            history=_history(body),
        ).to_dict()
    except LLMUnavailableError:
        refund_free_question(request, principal, plan)
        raise
    result["tier"] = plan["tier"]
    if free_remaining is not None:
        result["free_remaining"] = free_remaining
    if app.state.auth_enabled:
        app.state.auth.audit(
            principal.username, "query", detail=question_hash(body.question)
        )
    return result
