"""LLM failure taxonomy: SDK exceptions map to LLMUnavailableError kinds,
the escalation call is best-effort, and a primary failure propagates."""

import anthropic
import httpx
import pytest

from core.generation.llm_client import AnthropicClient, LLMUnavailableError

REQ = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _status_exc(cls, code):
    return cls("boom", response=httpx.Response(code, request=REQ), body=None)


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (_status_exc(anthropic.AuthenticationError, 401), "auth"),
        (_status_exc(anthropic.PermissionDeniedError, 403), "auth"),
        (_status_exc(anthropic.RateLimitError, 429), "rate_limited"),
        (_status_exc(anthropic.InternalServerError, 529), "overloaded"),
        (_status_exc(anthropic.BadRequestError, 400), "upstream"),
        (anthropic.APIConnectionError(request=REQ), "network"),
        (anthropic.APITimeoutError(request=REQ), "network"),
    ],
)
def test_sdk_exception_mapping(monkeypatch, exc, kind):
    client = AnthropicClient("claude-test", api_key="sk-ant-test")

    def raise_it(**kwargs):
        raise exc

    monkeypatch.setattr(client._client.messages, "create", raise_it)
    with pytest.raises(LLMUnavailableError) as caught:
        client.complete("system", "user")
    assert caught.value.kind == kind


# --- pipeline behaviour on failures -----------------------------------------


class FakeLLM:
    name = "fake"

    def __init__(self, responses):
        self._responses = list(responses)

    def complete(self, system, user):
        return self._responses.pop(0)


class RaisingLLM:
    name = "raising"

    def __init__(self, kind="overloaded"):
        self.kind = kind

    def complete(self, system, user):
        raise LLMUnavailableError(self.kind)


def test_escalation_failure_keeps_primary_answer(seeded_pipeline):
    seeded_pipeline.llm = FakeLLM(["Partial [1].\nINSUFFICIENT_SOURCES"])
    seeded_pipeline.escalation_llm = RaisingLLM()

    result = seeded_pipeline.query("Do I need a data protection officer?")
    assert not result.escalated
    assert result.insufficient  # honesty flag survives the failed retry
    assert "Partial" in result.answer


def test_primary_failure_propagates(seeded_pipeline):
    seeded_pipeline.llm = RaisingLLM("network")
    seeded_pipeline.escalation_llm = None

    with pytest.raises(LLMUnavailableError):
        seeded_pipeline.query("Do I need a data protection officer?")


def test_request_scoped_escalation_with_no_server_client(seeded_pipeline, monkeypatch):
    # regression: the escalation log line read self.escalation_llm.name — on a
    # BYOK-only server that attribute is None and the cascade crashed
    primary = FakeLLM(["Partial [1].\nINSUFFICIENT_SOURCES"])
    escalation = FakeLLM(["Escalated [1]."])
    seeded_pipeline.escalation_llm = None
    monkeypatch.setattr(
        seeded_pipeline,
        "_resolve_llm",
        lambda model, key: escalation if model == "esc-model" else primary,
    )

    result = seeded_pipeline.query(
        "Do I need a data protection officer?",
        answer_model="prim-model",
        escalation_model="esc-model",
        api_key="sk-ant-user-key",
    )
    assert result.escalated
    assert result.answer == "Escalated [1]."
