from core.generation.citations import (
    build_citations,
    build_context,
    markers_used,
    validate_answer,
)
from core.ingestion.chunker import Chunk


def chunk(i: int) -> Chunk:
    return Chunk(
        chunk_id=f"doc:{i}",
        doc_id="doc",
        index=i,
        text=f"Text of chunk {i}. " * 30,
        title=f"Doc title {i}",
        source_url=f"https://example.eu/{i}",
    )


def test_markers_used_parses_all_forms():
    assert markers_used("Claim [1]. Another [2], repeated [1]. [12]") == {1, 2, 12}


def test_validate_rejects_uncited_answer():
    ok, reason = validate_answer("Confident claim with no citations.", n_sources=3)
    assert not ok
    assert "no citations" in reason


def test_validate_rejects_out_of_range_marker():
    ok, reason = validate_answer("Claim [1] and fabricated [7].", n_sources=3)
    assert not ok
    assert "[7]" in reason


def test_validate_accepts_resolvable_citations():
    ok, _ = validate_answer("Claim [1], more [3].", n_sources=3)
    assert ok


def test_build_citations_markers_are_one_based_and_ordered():
    citations = build_citations([chunk(0), chunk(1)])
    assert [c.marker for c in citations] == [1, 2]
    assert citations[0].chunk_id == "doc:0"
    assert citations[0].quote in chunk(0).text


def test_build_context_numbers_sources():
    context = build_context([chunk(0), chunk(1)])
    assert "[1] Doc title 0" in context
    assert "[2] Doc title 1" in context
