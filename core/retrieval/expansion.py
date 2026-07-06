"""Query expansion: HyDE and multi-hop decomposition.

Questions and legal passages live in different registers ("Can I fire someone
for reporting fraud?" vs "Member States shall prohibit any form of
retaliation…"). Both helpers here use a small, cheap model to bridge that gap
at query time; retrieval works unchanged without them, and any LLM failure
falls back to the raw query. Defaults are set by measurement on the golden
harness (docs/DEVLOG.md).
"""

import logging

logger = logging.getLogger(__name__)

_HYDE_SYSTEM = (
    "You draft hypothetical excerpts of EU legislation. Given a question, "
    "write a short passage (2–4 sentences) in the formal style of an EU "
    "regulation or directive that would answer it — plausible article-style "
    "language, no preamble, no mention that it is hypothetical."
)

_DECOMPOSE_SYSTEM = (
    "You split compound legal questions. If the question asks about multiple "
    "distinct legal topics, rewrite it as self-contained sub-questions, one "
    "per line, at most three, nothing else. If it is a single question, "
    "respond with the single word NONE."
)


class HydeExpander:
    """Returns the text the vector leg should embed. BM25 always keeps the
    raw question — regulation numbers and exact terms must stay literal."""

    def __init__(self, llm):
        self._llm = llm
        self.name = f"hyde:{llm.name}"

    def expand(self, query: str) -> str:
        try:
            passage = self._llm.complete(_HYDE_SYSTEM, query).strip()
        except Exception as exc:
            logger.warning("HyDE unavailable (%s) — using raw query", exc)
            return query
        return f"{query}\n\n{passage}" if passage else query


class QueryDecomposer:
    """Returns sub-questions for compound questions, [] otherwise."""

    def __init__(self, llm):
        self._llm = llm
        self.name = f"decompose:{llm.name}"

    def subqueries(self, query: str) -> list[str]:
        try:
            reply = self._llm.complete(_DECOMPOSE_SYSTEM, query).strip()
        except Exception as exc:
            logger.warning("decomposition unavailable (%s) — skipping", exc)
            return []
        lines = [ln.strip("-•* \t") for ln in reply.splitlines() if ln.strip()]
        if not lines or lines[0].upper().startswith("NONE") or len(lines) < 2:
            return []
        return lines[:3]
