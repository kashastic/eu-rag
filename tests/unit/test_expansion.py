"""HyDE + decomposition: parsing, fallbacks, and retriever integration."""

from core.retrieval.bm25 import BM25Index
from core.retrieval.expansion import HydeExpander, QueryDecomposer
from core.retrieval.hybrid_retriever import HybridRetriever


class FakeLLM:
    name = "fake"

    def __init__(self, reply=None, error=False):
        self.reply, self.error = reply, error

    def complete(self, system, user):
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
