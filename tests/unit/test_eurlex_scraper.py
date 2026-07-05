"""EUR-Lex scraper: verification and cache loading, fully offline."""

import pytest

from data.scrapers import eurlex
from data.scrapers.eurlex import (
    ShortlistEntry,
    VerificationError,
    document_from_html,
    load_cached_documents,
)

ENTRY = ShortlistEntry(
    "39999R9999",
    "Regulation (EU) 9999/42 — Fictional Widget Regulation",
    ("regulation (eu) 9999/42", "widgets"),
    tier=1,
)

GOOD_HTML = (
    "<html><body><p>REGULATION (EU) 9999/42 on the safety of widgets</p>"
    "<p>Article 1 — Widgets shall be safe.</p></body></html>"
)


def test_matching_document_is_built_with_provenance():
    doc = document_from_html(ENTRY, GOOD_HTML)
    assert doc.title == ENTRY.title
    assert doc.source_type == "eur-lex"
    assert doc.source_url == ENTRY.url
    assert "Article 1" in doc.text


def test_wrong_law_is_rejected():
    with pytest.raises(VerificationError):
        document_from_html(
            ENTRY, "<p>REGULATION (EU) 2016/679 on something else entirely</p>"
        )


def test_eurlex_error_page_is_rejected():
    # EUR-Lex answers unknown CELEX numbers with an HTTP 200 error page
    with pytest.raises(VerificationError):
        document_from_html(ENTRY, "<p>The requested document does not exist.</p>")


def test_load_cached_documents_skips_unverified_files(tmp_path, monkeypatch):
    monkeypatch.setattr(eurlex, "RAW_DIR", tmp_path)
    bad = ShortlistEntry("39999R0001", "Some Other Act", ("no such phrase",), tier=1)
    (tmp_path / f"{ENTRY.celex}.html").write_text(GOOD_HTML)
    (tmp_path / f"{bad.celex}.html").write_text("<p>wrong content</p>")

    docs = load_cached_documents([ENTRY, bad])
    assert set(docs) == {ENTRY.celex}


def test_pull_reports_mismatch_without_ingesting(monkeypatch):
    monkeypatch.setattr(
        eurlex, "fetch_html", lambda entry, force=False: "<p>the wrong law</p>"
    )

    class NeverIngest:
        def ingest(self, doc):
            raise AssertionError("a mismatched document reached the pipeline")

    results = eurlex.pull([ENTRY], pipeline=NeverIngest())
    assert results[0][1].startswith("SKIPPED")
