"""Stateless /query — the anonymous entry point.

Anonymous users get `free_anon_questions` full-quality answers (the Sonnet→Opus
cascade), counted server-side per IP/day. When that allowance is spent the
route returns 401 with code `anonymous_limit_reached`, the signal for the
frontend to raise its login wall. Logged-in users are tiered by BYOK:
own-key = full cascade billed to them; free = cheap model, no escalation.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from api.deps import (
    allowed_tenants,
    client_ip,
    optional_principal,
    paid_tier,
    refund_free_question,
    spend_free_question,
)
from core.generation.llm_client import LLMUnavailableError
from core.profile import AI_ROLES, COUNTRIES, SECTORS, SIZES, BusinessProfile
from core.security import turnstile
from core.security.auth import Principal, question_hash

router = APIRouter(tags=["query"])

_VOCABULARIES: dict[str, dict[str, str]] = {
    "country": COUNTRIES,
    "size": SIZES,
    "sector": SECTORS,
    "ai_role": AI_ROLES,
}


class ProfileBody(BaseModel):
    """The asker's business context, as sent by the client.

    This is the validation boundary that lets `core.profile.describe()` build a
    sentence for the trusted region of the prompt: every value is checked
    against a closed vocabulary, so an unknown one is a 422 here and never
    becomes prompt text. Free text must not be added to this model — see the
    module docstring in `core/profile.py`.

    All four fields are optional; the intro screen never requires an answer.
    """

    country: str | None = None
    size: str | None = None
    sector: str | None = None
    ai_role: str | None = None

    @field_validator("country", "size", "sector", "ai_role")
    @classmethod
    def _in_vocabulary(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        if value not in _VOCABULARIES[info.field_name]:
            raise ValueError(f"unknown {info.field_name}")
        return value

    def to_profile(self) -> BusinessProfile:
        return BusinessProfile(
            country=self.country,
            size=self.size,
            sector=self.sector,
            ai_role=self.ai_role,
        )


def profile_of(body: "QueryRequest | object") -> BusinessProfile | None:
    """The profile carried by a request, or None.

    The request is authoritative even for logged-in users, whose profile is
    also stored server-side: the client loads the stored copy at startup and
    sends it back, so there is no per-query database read and no merge rule to
    get wrong.
    """
    sent = getattr(body, "profile", None)
    return sent.to_profile() if sent is not None else None


# A prior turn is bounded because it is client-supplied text that ends up in a
# prompt. It is *truncated* rather than rejected: the only consumer is
# `QueryContextualizer.standalone`, which already reads no more than
# MAX_ANSWER_CHARS (400) of an answer and only the last few turns — so a cap
# that 422s is refusing a request over text the pipeline would have sliced off
# anyway. And the text in question is EURAG's own previous answer, so rejecting
# it fails the user for something they did not write.
HISTORY_CHARS = 2000


class HistoryTurn(BaseModel):
    """One prior exchange, oldest first.

    **Truncated, never rejected.** This used to be `max_length=2000` on both
    fields, which meant a third question in a thread whose second answer ran
    long returned `422 answer: String should have at most 2000 characters` —
    and because request validation runs before the route body, it failed ahead
    of the quota check and rendered in the transcript as the answer. Only the
    topic of a prior turn matters to the rewrite, so slicing is the correct
    behaviour and was what the original comment claimed to be doing.
    """

    question: str = ""
    answer: str = ""

    @field_validator("question", "answer", mode="before")
    @classmethod
    def _truncate(cls, value):
        if not isinstance(value, str):
            return value
        return value[:HISTORY_CHARS]


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    profile: ProfileBody | None = None
    # Deprecated, and deliberately accepted-but-ignored rather than removed:
    # tabs open across the deploy still post it, and rejecting it would 422 them
    # mid-session. It is not forwarded anywhere — free text no longer reaches
    # the prompt at all, which is the point of replacing it with `profile`.
    # Remove after one release.
    industry: str | None = Field(default=None, max_length=80, deprecated=True)
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
                profile=profile_of(body),
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
            profile=profile_of(body),
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
