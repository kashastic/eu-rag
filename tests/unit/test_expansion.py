"""Contextualisation + HyDE + decomposition: parsing, fallbacks, integration."""

from core.retrieval.bm25 import BM25Index
from core.retrieval.expansion import (
    HydeExpander,
    QueryContextualizer,
    QueryDecomposer,
)
from core.retrieval.hybrid_retriever import HybridRetriever


class FakeLLM:
    name = "fake"

    def __init__(self, reply=None, error=False):
        self.reply, self.error = reply, error
        self.prompts: list[str] = []

    def complete(self, system, user):
        self.prompts.append(user)
        if self.error:
            raise RuntimeError("api down")
        return self.reply


class RecordingEmbedder:
    def __init__(self):
        self.saw = []

    def embed_query(self, text):
        self.saw.append(text)
        return [0.0]


class _NoVectors:
    def search(self, vector, k, tenants=None):
        return []


_DPO_TURN = (
    "Do I need a data protection officer for a 30-person company?",
    "It does not depend on headcount; Article 37(1) GDPR turns on your core"
    " activities.",
)


def test_contextualizer_rewrites_follow_up_into_standalone_question():
    llm = FakeLLM("Do I need a data protection officer with 29 employees?")
    out = QueryContextualizer(llm).standalone("what if I have 29 people?", [_DPO_TURN])
    assert out == "Do I need a data protection officer with 29 employees?"
    # the prior turn must actually reach the prompt — that is the whole point
    assert "data protection officer" in llm.prompts[0]
    assert "what if I have 29 people?" in llm.prompts[0]


def test_contextualizer_no_history_is_a_no_op_and_costs_no_call():
    """First questions must not pay a Haiku call or risk a bad rewrite."""
    llm = FakeLLM("SHOULD NOT BE USED")
    assert QueryContextualizer(llm).standalone("Do I need a DPO?", []) == (
        "Do I need a DPO?"
    )
    assert llm.prompts == []


def test_contextualizer_falls_back_to_raw_query_on_error():
    out = QueryContextualizer(FakeLLM(error=True)).standalone("q", [_DPO_TURN])
    assert out == "q"


def test_contextualizer_rejects_unusable_rewrites():
    """A rewrite is only worth using if it is a single plain question. A model
    that explains itself, answers instead, or returns nothing must degrade to
    the raw query — a bad rewrite is worse than none, because it silently
    retrieves the wrong act."""
    for bad in ["", "   ", "Sure! Here is the question:\nDo I need a DPO?", "x" * 401]:
        out = QueryContextualizer(FakeLLM(bad)).standalone("follow up?", [_DPO_TURN])
        assert out == "follow up?", f"should have rejected {bad[:30]!r}"


def test_contextualizer_instructs_the_model_to_keep_the_language():
    """`answerer` promises to answer in the language the question was asked in,
    but it only ever sees the rewrite — so if this step translates, that
    promise is silently broken downstream. It was: an English follow-up came
    back answered in Spanish in production (2026-08-09). Behaviour depends on
    a model, so what is asserted here is that the constraint is still in the
    prompt at all — deleting the line would reintroduce the bug invisibly."""
    from core.retrieval.expansion import _CONTEXTUALIZE_SYSTEM

    lowered = _CONTEXTUALIZE_SYSTEM.lower()
    assert "same language as the follow-up question" in lowered
    assert "not translating" in lowered


def test_contextualizer_bounds_what_reaches_the_prompt():
    """History is client-supplied text going into a prompt: only the last few
    turns are used and answers are truncated."""
    llm = FakeLLM("rewritten")
    history = [(f"question {i}", "A" * 2000) for i in range(6)]
    QueryContextualizer(llm).standalone("follow up?", history)

    prompt = llm.prompts[0]
    assert "question 5" in prompt and "question 0" not in prompt
    assert prompt.count("A" * QueryContextualizer.MAX_ANSWER_CHARS) <= (
        QueryContextualizer.MAX_TURNS
    )
    assert "A" * (QueryContextualizer.MAX_ANSWER_CHARS + 1) not in prompt


def test_hyde_appends_passage_to_query():
    expander = HydeExpander(FakeLLM("The controller shall designate an officer."))
    out = expander.expand("Do I need a DPO?")
    assert out.startswith("Do I need a DPO?")
    assert "controller shall designate" in out


def test_hyde_falls_back_to_raw_query_on_error():
    assert HydeExpander(FakeLLM(error=True)).expand("q") == "q"


def test_decomposer_parses_subquestions():
    llm = FakeLLM("What guarantee applies to software?\nHow long is withdrawal?")
    subs = QueryDecomposer(llm).subqueries("compound question")
    assert len(subs) == 2


def test_decomposer_none_and_errors_yield_empty():
    assert QueryDecomposer(FakeLLM("NONE")).subqueries("simple?") == []
    assert QueryDecomposer(FakeLLM("only one line")).subqueries("simple?") == []
    assert QueryDecomposer(FakeLLM(error=True)).subqueries("q") == []


def _bm25(chunks: dict[str, str]) -> BM25Index:
    index = BM25Index()
    for cid, text in chunks.items():
        index.add(cid, text)
    return index


def test_hyde_rewrites_vector_leg_but_not_bm25():
    embedder = RecordingEmbedder()
    retriever = HybridRetriever(
        _bm25({"doc:0": "widget safety rules"}),
        _NoVectors(),
        embedder,
        expander=HydeExpander(FakeLLM("HYPOTHETICAL PASSAGE")),
    )
    ids = retriever.retrieve("widget safety", k=2)
    assert "HYPOTHETICAL PASSAGE" in embedder.saw[0]  # vector leg expanded
    assert ids == ["doc:0"]  # BM25 still matched the raw query


def test_decomposer_pulls_in_subquery_documents():
    chunks = {"docA:0": "alpha topic rules", "docB:0": "beta topic obligations"}

    class FakeDecomposer:
        def subqueries(self, query):
            return ["alpha topic?", "beta topic obligations?"]

    retriever = HybridRetriever(
        _bm25(chunks), _NoVectors(), RecordingEmbedder(), decomposer=FakeDecomposer()
    )
    ids = retriever.retrieve("alpha topic", k=4)
    assert "docB:0" in ids  # only reachable via the second sub-query
