"""Citation schema and enforcement — the product's load-bearing rule.

Every [N] in an answer must resolve to a retrieved chunk. Answers that cite
nothing, or cite markers outside the retrieved set, are rejected upstream.
"""

import re
from dataclasses import asdict, dataclass

from core.ingestion.chunker import Chunk

_MARKER_RE = re.compile(r"\[(\d{1,2})\]")


@dataclass
class Citation:
    marker: int
    chunk_id: str
    title: str
    source_url: str
    quote: str

    def to_dict(self) -> dict:
        return asdict(self)


def build_citations(chunks: list[Chunk]) -> list[Citation]:
    return [
        Citation(
            marker=i + 1,
            chunk_id=chunk.chunk_id,
            title=chunk.title,
            source_url=chunk.source_url,
            quote=chunk.text[:240],
        )
        for i, chunk in enumerate(chunks)
    ]


def build_context(chunks: list[Chunk]) -> str:
    blocks = []
    for i, chunk in enumerate(chunks):
        blocks.append(f"[{i + 1}] {chunk.title}\n{chunk.text}")
    return "\n\n---\n\n".join(blocks)


def markers_used(answer: str) -> set[int]:
    return {int(m) for m in _MARKER_RE.findall(answer)}


def validate_answer(answer: str, n_sources: int) -> tuple[bool, str]:
    """Returns (ok, reason). An answer must cite at least one source and
    every marker must resolve to a provided source."""
    used = markers_used(answer)
    if not used:
        return False, "answer contains no citations"
    out_of_range = {m for m in used if m < 1 or m > n_sources}
    if out_of_range:
        return False, f"citations {sorted(out_of_range)} do not resolve to any source"
    return True, ""
