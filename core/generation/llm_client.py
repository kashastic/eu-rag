"""LLM provider abstraction.

AnthropicClient when credentials are available; ExtractiveClient otherwise —
it quotes retrieved passages verbatim, so the product stays demoable (and
zero-hallucination) with no API key.
"""

import logging
import os
from typing import Protocol

logger = logging.getLogger(__name__)


class LLMClient(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


class AnthropicClient:
    def __init__(self, model: str, api_key: str | None = None):
        import anthropic

        # api_key set = BYOK (billed to the user); else the server's env key
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self._model = model
        self.name = f"anthropic:{model}"

    def complete(self, system: str, user: str) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in response.content if b.type == "text")


class ExtractiveClient:
    """Sentinel — the answerer detects this and composes an extractive answer
    itself instead of calling complete()."""

    name = "extractive"

    def complete(self, system: str, user: str) -> str:
        raise NotImplementedError("extractive mode is handled by the answerer")


def get_llm_client(model: str, api_key: str | None = None) -> LLMClient:
    """api_key set = BYOK: always use Anthropic with the user's key. Otherwise
    fall back to the server key, or extractive mode when none is configured."""
    if api_key:
        try:
            return AnthropicClient(model, api_key=api_key)
        except Exception as exc:
            logger.warning("BYOK client failed (%s) — extractive mode", exc)
            return ExtractiveClient()
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        try:
            return AnthropicClient(model)
        except Exception as exc:
            logger.warning("Anthropic client unavailable (%s) — extractive mode", exc)
    else:
        logger.warning(
            "No ANTHROPIC_API_KEY set — answers will be extractive (verbatim quotes)"
        )
    return ExtractiveClient()
