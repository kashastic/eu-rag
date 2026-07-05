from core.generation.answerer import NO_SOURCES_MESSAGE, answer_question
from core.generation.llm_client import ExtractiveClient
from core.ingestion.chunker import Chunk


class FakeLLM:
    """Scriptable LLM: returns queued responses in order."""

    name = "fake"

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        return self._responses.pop(0)


def chunks(n: int) -> list[Chunk]:
    return [
        Chunk(
            chunk_id=f"d:{i}",
            doc_id="d",
            index=i,
            text=f"Relevant passage {i} about GDPR erasure and notification.",
            title=f"Source {i}",
            source_url="https://eur-lex.europa.eu",
        )
        for i in range(n)
    ]


def test_no_chunks_yields_honest_refusal():
    result = answer_question("q", [], FakeLLM([]))
    assert result.mode == "no_sources"
    assert result.answer == NO_SOURCES_MESSAGE
    assert result.citations == []


def test_valid_llm_answer_keeps_only_used_citations():
    llm = FakeLLM(["Erasure is covered [1] and notification too [3]."])
    result = answer_question("q", chunks(3), llm)
    assert result.mode == "llm"
    assert {c.marker for c in result.citations} == {1, 3}


def test_invalid_citation_triggers_one_retry_then_extractive():
    llm = FakeLLM(["Fabricated [9].", "Still fabricated [8]."])
    result = answer_question("q", chunks(2), llm)
    assert llm.calls == 2
    assert result.mode == "extractive"
    # extractive answers still cite
    assert result.citations
    assert all(1 <= c.marker <= 2 for c in result.citations)


def test_retry_can_recover():
    llm = FakeLLM(["No citations here.", "Grounded claim [2]."])
    result = answer_question("q", chunks(2), llm)
    assert llm.calls == 2
    assert result.mode == "llm"
    assert [c.marker for c in result.citations] == [2]


def test_extractive_client_never_ships_uncited():
    result = answer_question("q", chunks(4), ExtractiveClient())
    assert result.mode == "extractive"
    assert result.citations
    assert result.answer.strip()


def test_insufficient_marker_is_detected_and_stripped():
    llm = FakeLLM(["Only partial coverage exists [1].\nINSUFFICIENT_SOURCES"])
    result = answer_question("q", chunks(2), llm)
    assert result.mode == "llm"
    assert result.insufficient
    assert "INSUFFICIENT_SOURCES" not in result.answer
    assert result.answer.endswith("[1].")


def test_confident_answer_is_not_marked_insufficient():
    result = answer_question("q", chunks(2), FakeLLM(["Fully answered [1][2]."]))
    assert not result.insufficient


def test_validation_fallback_counts_as_insufficient():
    llm = FakeLLM(["Fabricated [9].", "Still fabricated [8]."])
    result = answer_question("q", chunks(2), llm)
    assert result.mode == "extractive"
    assert result.insufficient


def test_no_sources_counts_as_insufficient():
    result = answer_question("q", [], FakeLLM([]))
    assert result.insufficient


class PromptCapturingLLM(FakeLLM):
    def complete(self, system: str, user: str) -> str:
        self.last_user = user
        return super().complete(system, user)


def test_industry_context_reaches_the_prompt():
    llm = PromptCapturingLLM(["Answer [1]."])
    answer_question("q", chunks(1), llm, industry="food & beverage")
    assert "food & beverage sector" in llm.last_user


def test_blank_industry_adds_no_context():
    llm = PromptCapturingLLM(["Answer [1]."])
    answer_question("q", chunks(1), llm, industry="   ")
    assert "sector" not in llm.last_user
