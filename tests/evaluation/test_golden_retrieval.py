"""Golden-question retrieval eval — pass/fail bar.

The bar enforced here is document-level hit@k with the offline hash embedder
(BM25 carries lexical quality). Chunk-level precision is measured, not
enforced, by `python -m core.evaluation.harness` (phrase_hit) — see
core/evaluation/golden.py for the shared cases.

Core cases run against whatever corpus is seeded — samples on a fresh clone,
real EUR-Lex full texts once `python -m data.scrapers.eurlex` has been run.
Extended cases (core=False) cover laws that only exist in the pulled corpus;
they skip rather than fail when their document is absent.
"""

import pytest

from core.evaluation.golden import CASES
from core.evaluation.harness import evaluate, evaluate_case

CORE = [c for c in CASES if c.core]
EXTENDED = [c for c in CASES if not c.core]


def _ids(cases):
    return [c.question[:50] for c in cases]


@pytest.mark.parametrize("case", CORE, ids=_ids(CORE))
def test_expected_document_is_retrieved(seeded_pipeline, case):
    result = evaluate_case(seeded_pipeline, case, k=6)
    assert result.retrieved_titles, "retrieval returned nothing"
    assert result.doc_hit, (
        f"expected a chunk from '{case.doc_marker}',"
        f" got documents: {result.retrieved_titles}"
    )


@pytest.mark.parametrize("case", EXTENDED, ids=_ids(EXTENDED))
def test_extended_corpus_document_is_retrieved(seeded_pipeline, case):
    titles = [d["title"].lower() for d in seeded_pipeline.registry.list_documents()]
    if not any(case.doc_marker.lower() in title for title in titles):
        pytest.skip(
            f"'{case.doc_marker}' not in corpus — run: python -m data.scrapers.eurlex"
        )
    result = evaluate_case(seeded_pipeline, case, k=6)
    assert result.doc_hit, (
        f"expected a chunk from '{case.doc_marker}',"
        f" got documents: {result.retrieved_titles}"
    )


def test_harness_summary_is_consistent(seeded_pipeline):
    report = evaluate(seeded_pipeline, k=6)
    s = report["summary"]
    assert s["cases"] + s["skipped"] == len(CASES)
    assert 0.0 <= s["doc_mrr"] <= s["doc_hit_rate"] <= 1.0
    hits = sum(bool(r["doc_rank"]) for r in report["results"])
    assert s["doc_hit_rate"] == hits / s["cases"]


def test_answers_over_golden_set_are_always_cited(seeded_pipeline):
    for case in CORE[:4]:
        result = seeded_pipeline.query(case.question)
        assert result.mode in ("extractive", "llm")
        assert result.citations, f"uncited answer for: {case.question}"
