"""Portal scraper: verification, excerpt policy, JS-shell guard — offline."""

import json

import pytest

from data.scrapers import funding_calls
from data.scrapers.portals import (
    EXCERPT_WORDS,
    PortalEntry,
    VerificationError,
    document_from_html,
)

ENTRY = PortalEntry(
    "xx_agency",
    "Agency X — funding for enterprises (national scheme)",
    "https://example.eu/funding",
    ("agency x",),
    "national-scheme",
    country="xx",
)


def _page(words: int) -> str:
    body = " ".join(f"word{i}" for i in range(words))
    return f"<html><body><p>Agency X funding programmes.</p><p>{body}</p></body></html>"


def test_verified_page_becomes_document_with_provenance():
    doc = document_from_html(ENTRY, _page(300))
    assert doc.source_type == "national-scheme"
    assert doc.source_url == ENTRY.url


def test_js_shell_is_rejected():
    with pytest.raises(VerificationError, match="words extracted"):
        document_from_html(ENTRY, "<html><body><p>Agency X</p></body></html>")


def test_wrong_page_is_rejected():
    wrong = _page(300).replace("Agency X", "Something Else")
    with pytest.raises(VerificationError, match="phrases not found"):
        document_from_html(ENTRY, wrong)


def test_national_pages_store_excerpt_not_full_mirror():
    doc = document_from_html(ENTRY, _page(EXCERPT_WORDS * 3))
    assert len(doc.text.split()) <= EXCERPT_WORDS + 20
    assert ENTRY.url in doc.text  # link-out note


def test_ec_pages_keep_full_text():
    entry = PortalEntry("ec_x", "X (EC portal)", "https://europa.eu/x", ("agency x",), "ec-portal")
    doc = document_from_html(entry, _page(EXCERPT_WORDS * 3))
    assert len(doc.text.split()) > EXCERPT_WORDS * 2


def _sedia(n_calls: int) -> dict:
    return {
        "totalResults": n_calls,
        "results": [
            {
                "metadata": {
                    "title": [f"Call {i}"],
                    "identifier": [f"HORIZON-2026-{i}"],
                    "deadlineDate": ["2026-09-22T00:00:00.000+0000"],
                    "status": ["31094502"],
                }
            }
            for i in range(n_calls)
        ],
    }


def test_funding_snapshot_document():
    doc = funding_calls.document_from_calls(_sedia(8))
    assert doc.source_type == "funding-calls"
    assert "HORIZON-2026-3" in doc.text
    assert "deadline 2026-09-22" in doc.text
    assert "verify at the portal" in doc.text  # freshness caveat


def test_funding_snapshot_rejects_empty_response():
    with pytest.raises(ValueError, match="usable calls"):
        funding_calls.document_from_calls(_sedia(2))


def test_funding_cache_roundtrip(tmp_path, monkeypatch):
    cache = tmp_path / "calls.json"
    monkeypatch.setattr(funding_calls, "CACHE_PATH", cache)
    assert funding_calls.load_cached_document() is None
    cache.write_text(json.dumps(_sedia(6)))
    doc = funding_calls.load_cached_document()
    assert doc is not None and "Call 5" in doc.text
