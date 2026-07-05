"""Retrieve → prompt → generate → validate citations.

Answers whose [N] references don't resolve are regenerated once, then
downgraded to extractive mode rather than shipped uncited.
"""

import logging
from dataclasses import dataclass, field

from core.generation.citations import (
    Citation,
    build_citations,
    build_context,
    markers_used,
    validate_answer,
)
from core.generation.llm_client import ExtractiveClient, LLMClient
from core.ingestion.chunker import Chunk

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are EURAG, a compliance and funding assistant for European small and \
medium businesses. Answer using ONLY the numbered sources provided. Cite every \
claim with its source marker, e.g. [1] or [2]. If the sources do not contain \
the information needed to answer the core question, say so plainly, cite \
nothing beyond what you actually used, and end your reply with the exact token \
INSUFFICIENT_SOURCES on its own final line (do not use the token when the \
sources do answer the question). Never invent regulations, article numbers, \
deadlines, or amounts. You are not a lawyer; for binding advice the user \
should consult one. Answer in the language of the question."""

# the model's structured low-confidence signal; stripped before shipping
INSUFFICIENT_MARKER = "INSUFFICIENT_SOURCES"


@dataclass
class AnswerResult:
    answer: str
    citations: list[Citation] = field(default_factory=list)
    mode: str = "llm"  # llm | extractive | no_sources
    insufficient: bool = False  # sources didn't answer the core question
    escalated: bool = False  # answered by the escalation model

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "citations": [c.to_dict() for c in self.citations],
            "mode": self.mode,
            "insufficient": self.insufficient,
            "escalated": self.escalated,
        }


NO_SOURCES_MESSAGE = (
    "I could not find anything in the current knowledge base that answers this. "
    "The corpus currently covers a limited set of EU regulations and funding "
    "schemes — this question may fall outside it."
)


def _extractive_answer(question: str, chunks: list[Chunk]) -> str:
    lines = [
        "The most relevant passages from official sources, quoted verbatim:",
        "",
    ]
    for i, chunk in enumerate(chunks[:3]):
        snippet = chunk.text[:500].strip()
        lines.append(f"> {snippet}\n— {chunk.title} [{i + 1}]")
        lines.append("")
    return "\n".join(lines).strip()


def answer_question(
    question: str,
    chunks: list[Chunk],
    llm: LLMClient,
    industry: str | None = None,
) -> AnswerResult:
    if not chunks:
        return AnswerResult(
            answer=NO_SOURCES_MESSAGE, mode="no_sources", insufficient=True
        )

    citations = build_citations(chunks)

    if isinstance(llm, ExtractiveClient):
        text = _extractive_answer(question, chunks)
        used = markers_used(text)
        return AnswerResult(
            answer=text,
            citations=[c for c in citations if c.marker in used],
            mode="extractive",
        )

    # sector context tailors wording only — retrieval never sees it, and the
    # corpus holds cross-sector law, so the model must flag sector gaps
    industry_line = (
        f"Context: the asker operates in the {industry.strip()} sector. Tailor "
        "the answer where the sector matters, and say plainly when "
        "sector-specific EU rules are not among the sources.\n\n"
        if industry and industry.strip()
        else ""
    )
    user_prompt = (
        f"Sources:\n\n{build_context(chunks)}\n\n"
        f"{industry_line}Question: {question}\n\n"
        "Answer with [N] citations."
    )
    for attempt in range(2):
        text = llm.complete(SYSTEM_PROMPT, user_prompt)
        insufficient = INSUFFICIENT_MARKER in text
        if insufficient:
            text = text.replace(INSUFFICIENT_MARKER, "").rstrip()
        ok, reason = validate_answer(text, n_sources=len(chunks))
        if ok:
            used = markers_used(text)
            return AnswerResult(
                answer=text,
                citations=[c for c in citations if c.marker in used],
                mode="llm",
                insufficient=insufficient,
            )
        logger.warning("citation validation failed (%s), attempt %d", reason, attempt + 1)

    # two invalid generations → fall back to verbatim quotes, never ship
    # uncited; counts as low confidence for escalation purposes
    text = _extractive_answer(question, chunks)
    used = markers_used(text)
    return AnswerResult(
        answer=text,
        citations=[c for c in citations if c.marker in used],
        mode="extractive",
        insufficient=True,
    )
