"""Cloudflare Turnstile verification for the anonymous boundary.

The anonymous tier exposes full-quality (Opus-capable) LLM calls, so the free
questions and registration are gated on a Turnstile token. Failure policy
(docs/SECURITY.md): fail-CLOSED on a missing token or an explicit
`success: false` from siteverify — that's a bot or a replayed token — but
fail-OPEN on network errors reaching Cloudflare, because the per-IP quota
still bounds abuse and a CF outage must not take down the anon funnel.
"""

import json
import logging
import ssl
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def _ssl_context() -> ssl.SSLContext:
    # macOS Python installs often lack a CA bundle; certifi ships with our deps
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _post(url: str, fields: dict[str, str], timeout: float) -> dict:
    """POST form fields, return the parsed JSON body. Module-level so tests
    monkeypatch the network away (same seam pattern as the scrapers)."""
    body = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(
        request, timeout=timeout, context=_ssl_context()
    ) as response:
        return json.loads(response.read().decode("utf-8"))


def verify(
    token: str | None,
    secret: str,
    remoteip: str | None = None,
    timeout: float = 3.0,
) -> bool:
    """True iff the token should be accepted. Missing/empty token is an
    immediate reject (no network call); a siteverify verdict is honoured;
    a network failure is logged and waved through (fail-open, see above)."""
    if not token:
        return False
    fields = {"secret": secret, "response": token}
    if remoteip:
        fields["remoteip"] = remoteip
    try:
        result = _post(SITEVERIFY_URL, fields, timeout)
    except Exception as exc:  # network / timeout / bad JSON — CF unreachable
        logger.warning("turnstile siteverify unreachable (%s) — failing open", exc)
        return True
    if result.get("success") is True:
        return True
    logger.info(
        "turnstile rejected token: %s", result.get("error-codes", "no error codes")
    )
    return False
