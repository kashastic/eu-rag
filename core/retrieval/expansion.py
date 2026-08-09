"""Query rewriting before retrieval: contextualisation, HyDE, decomposition.

Questions and legal passages live in different registers ("Can I fire someone
for reporting fraud?" vs "Member States shall prohibit any form of
retaliation…"). The helpers here use a small, cheap model to bridge that gap
at query time; retrieval works unchanged without them, and any LLM failure
falls back to the raw query. Defaults are set by measurement on the golden
harness (docs/DEVLOG.md).

Order matters: contextualisation runs FIRST. A follow-up ("what if I have 29
people?") carries no topic of its own, so HyDE would expand the wrong thing
and BM25 would match on the wrong numbers — see docs/UPDATE_LOG.md.
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

_CONTEXTUALIZE_SYSTEM = (
    "You rewrite a follow-up question so it stands on its own, using the "
    "conversation that came before it.\n"
    "Resolve pronouns and elliptical references ('what about 29?', 'and "
    "then?', 'is that still true in France?') into the full subject, and "
    "carry over the legal topic the conversation is about.\n"
    "Rules: output ONLY the rewritten question, one line, no preamble and no "
    "explanation. Keep the user's own wording and specifics wherever you can "
    "— you are resolving references, not rephrasing or answering. Never add "
    "facts, article numbers, or legal conclusions that the conversation does "
    "not already contain. If the question already stands on its own, output "
    "it unchanged."
)


class QueryContextualizer:
    """Rewrites a follow-up into a standalone question using prior turns.

    This runs before retrieval AND decides what the answerer is asked, so it
    fixes both halves of the same bug: a bare "what if I have 29 people?"
    retrieved the Pay Transparency Directive (the only corpus match for a
    headcount) and was answered "too vague on its own". Feeding the rewritten
    question downstream means the answerer never sees the fragment, so
    cite-or-fail is untouched.

    Prior answers are included but truncated: they are model output being fed
    back into a prompt, and only their topic is load-bearing here.
    """

    #: prior turns are trimmed before they reach the prompt — the topic is
    #: what matters, and full answers are long, expensive, and mostly citations
    MAX_TURNS = 3
    MAX_ANSWER_CHARS = 400

    def __init__(self, llm):
        self._llm = llm
        self.name = f"contextualize:{llm.name}"

    def standalone(self, query: str, history: list[tuple[str, str]]) -> str:
        """history: prior (question, answer) pairs, oldest first. Returns the
        query unchanged when history is empty or the rewrite fails — a bad
        rewrite must never be worse than no rewrite."""
        if not history:
            return query
        turns = []
        for question, answer in history[-self.MAX_TURNS :]:
            turns.append(f"Q: {question}")
            if answer:
                turns.append(f"A: {answer[: self.MAX_ANSWER_CHARS]}")
        prompt = "\n".join(turns) + f"\n\nFollow-up question: {query}"
        try:
            rewritten = self._llm.complete(_CONTEXTUALIZE_SYSTEM, prompt).strip()
        except Exception as exc:
            logger.warning("contextualisation unavailable (%s) — raw query", exc)
            return query
        # a model that ignores the instruction and explains itself, returns
        # nothing, or answers the question instead must not poison retrieval
        if not rewritten or "\n" in rewritten or len(rewritten) > 400:
            logger.warning("contextualisation returned an unusable rewrite — raw query")
            return query
        if rewritten != query:
            logger.info("contextualised follow-up: %r -> %r", query, rewritten)
        return rewritten


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
