"""Retrieval eval harness: per-change before/after numbers over the golden set.

Usage:
    python -m core.evaluation.harness                 # table on stdout
    python -m core.evaluation.harness --json out.json # machine-readable
    python -m core.evaluation.harness --k 6 --label baseline

Metrics (per case and aggregate):
- doc_hit@k  — a chunk from the expected document is in the top k
- doc_mrr    — 1/rank of the first such chunk (0 on miss)
- phrase_hit — a top-k chunk from the expected document contains one of the
  case's verbatim phrases, i.e. retrieval surfaced the passage that actually
  answers the question (chunk-level precision; this is what a reranker buys)

Runs against whatever Settings say (live fastembed corpus by default; the
embedded stores are single-process, so stop the API server first). Compare
runs with different EURAG_RERANKER / EURAG_EMBEDDER values.
"""

import argparse
import json
from dataclasses import asdict, dataclass

from core.evaluation.golden import CASES, GoldenCase, marker_present
from core.pipeline import Pipeline


@dataclass
class CaseResult:
    question: str
    doc_marker: str
    doc_rank: int | None  # 1-based rank of first expected-doc chunk
    phrase_hit: bool | None  # None when the case defines no phrases
    all_docs_hit: bool | None  # compound cases: every required doc in top-k
    retrieved_titles: list[str]

    @property
    def doc_hit(self) -> bool:
        return self.doc_rank is not None


def evaluate_case(pipeline: Pipeline, case: GoldenCase, k: int) -> CaseResult:
    chunk_ids = pipeline.retriever.retrieve(case.question, k=k)
    chunks = pipeline.registry.get_chunks(chunk_ids)
    # "A|B" = alternatives: any of these documents legitimately answers
    markers = [m.strip().lower() for m in case.doc_marker.split("|")]

    def marked(chunk) -> bool:
        return any(m in chunk.title.lower() for m in markers)

    doc_rank = None
    for rank, chunk in enumerate(chunks, start=1):
        if marked(chunk):
            doc_rank = rank
            break

    phrase_hit = None
    if case.phrases:
        phrase_hit = any(
            marked(chunk)
            and any(p.lower() in chunk.text.lower() for p in case.phrases)
            for chunk in chunks
        )

    all_docs_hit = None
    if case.requires_all:
        titles = [c.title.lower() for c in chunks]
        all_docs_hit = all(
            any(m.lower() in t for t in titles) for m in case.requires_all
        )

    return CaseResult(
        question=case.question,
        doc_marker=case.doc_marker,
        doc_rank=doc_rank,
        phrase_hit=phrase_hit,
        all_docs_hit=all_docs_hit,
        retrieved_titles=[c.title for c in chunks],
    )


def evaluate(pipeline: Pipeline, k: int = 6) -> dict:
    corpus_titles = [d["title"].lower() for d in pipeline.registry.list_documents()]
    results: list[CaseResult] = []
    skipped: list[str] = []
    for case in CASES:
        needed = set(case.requires_all) | {case.doc_marker}
        if not case.core and not all(
            marker_present(m, corpus_titles) for m in needed
        ):
            skipped.append(case.question)
            continue
        results.append(evaluate_case(pipeline, case, k))

    n = len(results)
    with_phrases = [r for r in results if r.phrase_hit is not None]
    compound = [r for r in results if r.all_docs_hit is not None]
    summary = {
        "k": k,
        "cases": n,
        "skipped": len(skipped),
        "doc_hit_rate": sum(r.doc_hit for r in results) / n if n else 0.0,
        "doc_mrr": sum(1 / r.doc_rank for r in results if r.doc_rank) / n if n else 0.0,
        "phrase_hit_rate": (
            sum(r.phrase_hit for r in with_phrases) / len(with_phrases)
            if with_phrases
            else None
        ),
        "compound_hit_rate": (
            sum(r.all_docs_hit for r in compound) / len(compound) if compound else None
        ),
    }
    return {"summary": summary, "results": [asdict(r) for r in results]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--k", type=int, default=6)
    parser.add_argument("--json", help="also write full results to this path")
    parser.add_argument("--label", default="", help="tag printed with the summary")
    args = parser.parse_args()

    pipeline = Pipeline()
    try:
        report = evaluate(pipeline, k=args.k)
        config = (
            f"embedder={pipeline.embedder.name}"
            f" reranker={getattr(pipeline.retriever.reranker, 'name', None) or 'none'}"
        )
    finally:
        pipeline.close()

    print(f"\n{args.label or 'eval'} — {config}\n")
    for r in report["results"]:
        rank = f"rank {r['doc_rank']}" if r["doc_rank"] else "MISS   "
        phrase = {True: "phrase ✓", False: "phrase ✗", None: "        "}[r["phrase_hit"]]
        multi = {True: "multi ✓", False: "multi ✗", None: "       "}[r["all_docs_hit"]]
        print(
            f"  {rank:8} {phrase} {multi}  [{r['doc_marker'][:20]:20}]"
            f" {r['question'][:52]}"
        )
    s = report["summary"]
    phrase_rate = (
        f"{s['phrase_hit_rate']:.0%}" if s["phrase_hit_rate"] is not None else "n/a"
    )
    compound_rate = (
        f"{s['compound_hit_rate']:.0%}" if s["compound_hit_rate"] is not None else "n/a"
    )
    print(
        f"\n  k={s['k']}  cases={s['cases']} (+{s['skipped']} skipped)"
        f"  doc_hit={s['doc_hit_rate']:.0%}  doc_mrr={s['doc_mrr']:.2f}"
        f"  phrase_hit={phrase_rate}  compound_hit={compound_rate}"
    )

    if args.json:
        report["label"] = args.label
        report["config"] = config
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"  written: {args.json}")


if __name__ == "__main__":
    main()
