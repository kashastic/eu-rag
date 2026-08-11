"""Turnstile at the API boundary: anonymous /query and /auth/register are
gated when EURAG_TURNSTILE_SECRET is set, and a rejected challenge never
burns an anonymous free question. With no secret configured the gate is off
entirely — that path is what every other API suite exercises."""

import pytest
from fastapi.testclient import TestClient

from core.security import turnstile


@pytest.fixture()
def client(settings, monkeypatch):
    monkeypatch.setenv("EURAG_AUTH_ENABLED", "true")
    monkeypatch.setenv("EURAG_JWT_SECRET", "test-secret-at-least-32-bytes-long!!")
    monkeypatch.setenv("EURAG_FREE_ANON_QUESTIONS", "3")
    monkeypatch.setenv("EURAG_TURNSTILE_SECRET", "srv-secret")
    monkeypatch.setenv("EURAG_TURNSTILE_SITEKEY", "site-key-abc")
    from api.main import app

    with TestClient(app) as c:
        yield c


def _pass(monkeypatch):
    monkeypatch.setattr(turnstile, "_post", lambda u, f, t: {"success": True})


def _fail(monkeypatch):
    monkeypatch.setattr(turnstile, "_post", lambda u, f, t: {"success": False})


def test_healthz_serves_sitekey(client):
    assert client.get("/healthz").json()["turnstile_sitekey"] == "site-key-abc"


def test_anon_query_without_token_is_403_and_quota_untouched(client, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no token → no siteverify call")

    monkeypatch.setattr(turnstile, "_post", boom)
    r = client.post("/query", json={"question": "What is an SME?"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "turnstile_failed"

    # a failed challenge must not have spent a free question
    _pass(monkeypatch)
    ok = client.post(
        "/query", json={"question": "What is an SME?", "turnstile_token": "tok"}
    )
    assert ok.status_code == 200
    assert ok.json()["tier"] == "anonymous"
    assert ok.json()["anon_remaining"] == 2  # full allowance minus only this one


def test_anon_query_with_rejected_token_is_403(client, monkeypatch):
    _fail(monkeypatch)
    r = client.post(
        "/query", json={"question": "What is an SME?", "turnstile_token": "bad"}
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "turnstile_failed"


def test_anon_query_with_valid_token_answers(client, monkeypatch):
    _pass(monkeypatch)
    r = client.post(
        "/query", json={"question": "What is an SME?", "turnstile_token": "tok"}
    )
    assert r.status_code == 200 and r.json()["tier"] == "anonymous"


def test_register_without_token_is_403(client, monkeypatch):
    _fail(monkeypatch)
    r = client.post(
        "/auth/register", json={"username": "eve", "password": "longpassword1"}
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "turnstile_failed"


def test_register_with_valid_token_succeeds_and_login_ungated(client, monkeypatch):
    _pass(monkeypatch)
    r = client.post(
        "/auth/register",
        json={"username": "alice", "password": "longpassword1", "turnstile_token": "t"},
    )
    assert r.status_code == 200

    # login is never turnstile-gated — kill the network seam to prove it
    def boom(*a, **k):
        raise AssertionError("login must not call siteverify")

    monkeypatch.setattr(turnstile, "_post", boom)
    assert (
        client.post(
            "/auth/login", json={"username": "alice", "password": "longpassword1"}
        ).status_code
        == 200
    )


def test_logged_in_query_not_gated(client, monkeypatch):
    _pass(monkeypatch)
    client.post(
        "/auth/register",
        json={"username": "bob", "password": "longpassword1", "turnstile_token": "t"},
    )
    tok = client.post(
        "/auth/login", json={"username": "bob", "password": "longpassword1"}
    ).json()["access_token"]

    def boom(*a, **k):
        raise AssertionError("authed queries must not call siteverify")

    monkeypatch.setattr(turnstile, "_post", boom)
    r = client.post(
        "/query",
        json={"question": "SME thresholds?"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200 and r.json()["tier"] == "free"


# --- the widget's footprint in the web app ---------------------------------
#
# Parsed from the CSS because the web app has no test runner, and this exact
# class of bug has now shipped three times: an always-visible checkbox that
# disabled Ask and Create-account, a /login form gated on a widget it never
# rendered, and a solved challenge whose success state stayed above the
# composer for the rest of the session. All three were invisible locally,
# because with no EURAG_TURNSTILE_SECRET the gate is skipped entirely.

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent


def _css() -> str:
    return (REPO / "frontend/web/app/globals.css").read_text()


def _rule(css: str, selector: str) -> str:
    """The declaration block for an exact selector."""
    match = re.search(
        r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css
    )
    assert match, f"no rule found for {selector}"
    return match.group(1)


def test_widget_takes_no_space_unless_a_challenge_is_on_screen():
    """Cloudflare leaves its ~65px success state in the container after a
    solved challenge, so the container's footprint has to be driven by our own
    `active` class or the widget never gives the space back. It sat above the
    composer for the rest of the session until this rule existed."""
    base = _rule(_css(), ".turnstile")

    assert "height: 0" in base
    assert "overflow: hidden" in base


def test_a_running_challenge_is_visible_and_unclipped():
    """The other half: while a challenge is in flight it must have room and
    must not be clipped — Turnstile's interactive challenge can expand beyond
    the widget box, so overflow comes back with the height.

    This is `.open` (in flight OR interactive) rather than `.active`
    (interactive) on purpose. Hanging visibility on the interactive flag alone
    would mean a challenge that paints without firing
    before-interactive-callback is clipped to nothing — an unsolvable invisible
    challenge and a hung submit, which is worse than the leftover widget the
    collapse exists to remove."""
    openrule = _rule(_css(), ".turnstile.open")

    assert "height: auto" in openrule
    assert "overflow: visible" in openrule


def test_the_widget_is_never_hidden_with_display_or_visibility():
    """Turnstile can fail outright when its widget is removed from rendering,
    and the challenge must stay executable while collapsed. Collapsing is
    height-based on purpose; don't 'simplify' it to display:none."""
    for selector in (".turnstile", ".turnstile.open", ".turnstile.active"):
        block = _rule(_css(), selector)
        assert "display: none" not in block
        assert "visibility: hidden" not in block


def test_the_component_opens_the_box_for_every_challenge_it_runs():
    """The CSS is only half the contract — `.open` has to actually be applied
    for the whole in-flight window, and dropped when the challenge settles.
    Parsed from the component because the web app has no test runner."""
    tsx = (REPO / "frontend/web/components/Turnstile.tsx").read_text()

    # opened when a challenge starts, closed on every terminal outcome
    assert "setBusy(true)" in tsx
    assert "setBusy(false)" in tsx
    # and the class is driven by in-flight OR interactive, not interactive alone
    assert "busy || interactive" in tsx
