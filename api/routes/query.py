"""Stateless /query — the anonymous entry point.

Anonymous users get `free_anon_questions` full-quality answers (the Sonnet→Opus
cascade), counted server-side per IP/day. When that allowance is spent the
route returns 401 with code `anonymous_limit_reached`, the signal for the
frontend to raise its login wall. Logged-in users are tiered by BYOK:
own-key = full cascade billed to them; free = cheap model, no escalation.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import allowed_tenants, client_ip, optional_principal, paid_tier
from core.generation.llm_client import LLMUnavailableError
from core.security import turnstile
from core.security.auth import Principal, question_hash

router = APIRouter(tags=["query"])


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    industry: str | None = Field(default=None, max_length=80)
    turnstile_token: str | None = Field(default=None, max_length=2048)


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
            ).to_dict()
        except LLMUnavailableError:
            # the question was consumed up front (parallel-overrun safety) but
            # never answered — give it back before the error handler responds
            app.state.anon_quota.refund(key)
            raise
        result["tier"] = "anonymous"
        result["anon_remaining"] = remaining
        return result

    # logged in — tier by BYOK
    plan = paid_tier(request, principal)
    result = pipeline.query(
        body.question,
        industry=body.industry,
        tenants=allowed_tenants(request, principal),
        answer_model=plan["answer_model"],
        escalation_model=plan["escalation_model"],
        api_key=plan["api_key"],
    ).to_dict()
    result["tier"] = plan["tier"]
    if app.state.auth_enabled:
        app.state.auth.audit(
            principal.username, "query", detail=question_hash(body.question)
        )
    return result
