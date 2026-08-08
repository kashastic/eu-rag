"""Turnstile verify(): reject without a network call on missing tokens,
honour the siteverify verdict, fail open when Cloudflare is unreachable."""

from core.security import turnstile


def test_missing_token_rejected_without_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("siteverify must not be called for a missing token")

    monkeypatch.setattr(turnstile, "_post", boom)
    assert turnstile.verify(None, "secret") is False
    assert turnstile.verify("", "secret") is False


def test_success_verdict_accepted_and_fields_sent(monkeypatch):
    calls = {}

    def fake_post(url, fields, timeout):
        calls["url"], calls["fields"] = url, fields
        return {"success": True}

    monkeypatch.setattr(turnstile, "_post", fake_post)
    assert turnstile.verify("tok", "sec", remoteip="1.2.3.4") is True
    assert calls["url"] == turnstile.SITEVERIFY_URL
    assert calls["fields"] == {"secret": "sec", "response": "tok", "remoteip": "1.2.3.4"}


def test_remoteip_omitted_when_unknown(monkeypatch):
    seen = {}

    def fake_post(url, fields, timeout):
        seen.update(fields)
        return {"success": True}

    monkeypatch.setattr(turnstile, "_post", fake_post)
    assert turnstile.verify("tok", "sec") is True
    assert "remoteip" not in seen


def test_explicit_failure_rejected(monkeypatch):
    monkeypatch.setattr(
        turnstile,
        "_post",
        lambda u, f, t: {"success": False, "error-codes": ["invalid-input-response"]},
    )
    assert turnstile.verify("bad", "sec") is False


def test_verdict_without_success_field_rejected(monkeypatch):
    # a reachable siteverify that doesn't say success=true is a rejection,
    # not a network failure — no fail-open
    monkeypatch.setattr(turnstile, "_post", lambda u, f, t: {"weird": 1})
    assert turnstile.verify("tok", "sec") is False


def test_network_failure_fails_open(monkeypatch, caplog):
    def down(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(turnstile, "_post", down)
    with caplog.at_level("WARNING"):
        assert turnstile.verify("tok", "sec") is True
    assert "failing open" in caplog.text
