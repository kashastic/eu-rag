"""Seed CLI (--expect-docs, fresh-clone behaviour), funding-snapshot hash
stability, and the strict-boot embedder guard."""

import json
import os
from datetime import date, datetime

import pytest

import data.seed as seed_mod
from core.ingestion.embedder import HashingEmbedder, get_embedder
from data.scrapers import funding_calls


@pytest.fixture()
def fresh_clone(settings, tmp_path, monkeypatch):
    """Simulate a clean checkout: no scraper caches, only bundled samples."""
    monkeypatch.setattr(seed_mod, "load_cached_documents", lambda: {})
    monkeypatch.setattr(seed_mod.portals, "load_cached_documents", lambda: {})
    monkeypatch.setattr(seed_mod.funding_calls, "load_cached_document", lambda: None)
    # keep the lock file out of the repo's real data/raw
    monkeypatch.setattr(seed_mod, "RAW_ROOT", tmp_path / "raw")
    return settings


def test_expect_docs_fails_on_partial_corpus(fresh_clone, capsys):
    with pytest.raises(SystemExit) as excinfo:
        seed_mod.main(["--expect-docs", "47"])
    assert "corpus incomplete" in str(excinfo.value)


def test_expect_docs_passes_when_met(fresh_clone, capsys):
    seed_mod.main(["--expect-docs", "4"])  # the 4 bundled samples
    out = capsys.readouterr().out
    assert "4 in registry" in out


def test_plain_seed_unchanged_without_flags(fresh_clone, capsys):
    seed_mod.main([])  # no flags: seeds samples, exits 0, no scraping
    assert "Seeded 4 documents" in capsys.readouterr().out


def _fake_calls(n=6):
    return {
        "totalResults": n,
        "results": [
            {
                "metadata": {
                    "title": [f"Call {i}"],
                    "identifier": [f"HORIZON-{i}"],
                    "deadlineDate": ["2026-12-31T00:00:00"],
                    "status": ["31094502"],
                }
            }
            for i in range(n)
        ],
    }


def test_funding_snapshot_hash_is_date_stable():
    day = date(2026, 1, 2)
    a = funding_calls.document_from_calls(_fake_calls(), snapshot_date=day)
    b = funding_calls.document_from_calls(_fake_calls(), snapshot_date=day)
    assert a.content_hash == b.content_hash
    assert "2026-01-02" in a.text


def test_cached_funding_snapshot_uses_mtime_not_today(tmp_path, monkeypatch):
    cache = tmp_path / "funding" / "calls.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps(_fake_calls()), encoding="utf-8")
    mtime = datetime(2026, 3, 15, 12).timestamp()
    os.utime(cache, (mtime, mtime))
    monkeypatch.setattr(funding_calls, "CACHE_PATH", cache)

    first = funding_calls.load_cached_document()
    second = funding_calls.load_cached_document()
    assert first is not None and second is not None
    assert "2026-03-15" in first.text
    assert date.today().isoformat() not in first.text
    assert first.content_hash == second.content_hash


def test_get_embedder_strict_raises(monkeypatch):
    def boom(model_name):
        raise OSError("no network, no model")

    monkeypatch.setattr("core.ingestion.embedder.FastEmbedEmbedder", boom)
    with pytest.raises(RuntimeError, match="STRICT_BOOT"):
        get_embedder("fastembed", "some-model", strict=True)


def test_get_embedder_lenient_falls_back(monkeypatch):
    def boom(model_name):
        raise OSError("no network, no model")

    monkeypatch.setattr("core.ingestion.embedder.FastEmbedEmbedder", boom)
    embedder = get_embedder("fastembed", "some-model", strict=False)
    assert isinstance(embedder, HashingEmbedder)
