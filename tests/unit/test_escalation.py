"""Low-confidence cascade: cheap model answers, escalation model is consulted
only when the cheap answer signals insufficiency."""


class FakeLLM:
    name = "fake"

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        return self._responses.pop(0)


def test_insufficient_answer_escalates(seeded_pipeline):
    primary = FakeLLM(["I only found part of this [1].\nINSUFFICIENT_SOURCES"])
    escalation = FakeLLM(["Complete escalated answer [1]."])
    seeded_pipeline.llm = primary
    seeded_pipeline.escalation_llm = escalation

    result = seeded_pipeline.query("Do I need a data protection officer?")
    assert primary.calls == 1
    assert escalation.calls == 1
    assert result.escalated
    assert not result.insufficient
    assert result.answer == "Complete escalated answer [1]."
    assert result.citations


def test_confident_answer_never_touches_escalation_model(seeded_pipeline):
    primary = FakeLLM(["Fully answered from the sources [1]."])
    escalation = FakeLLM([])
    seeded_pipeline.llm = primary
    seeded_pipeline.escalation_llm = escalation

    result = seeded_pipeline.query("What are the SME turnover thresholds?")
    assert escalation.calls == 0
    assert not result.escalated


def test_still_honest_when_escalation_also_insufficient(seeded_pipeline):
    seeded_pipeline.llm = FakeLLM(["Partial [1].\nINSUFFICIENT_SOURCES"])
    seeded_pipeline.escalation_llm = FakeLLM(
        ["Even with more sources, partial [1].\nINSUFFICIENT_SOURCES"]
    )

    result = seeded_pipeline.query("Something the corpus cannot answer?")
    assert result.escalated
    assert result.insufficient  # honesty flag survives escalation
    assert "INSUFFICIENT_SOURCES" not in result.answer


def test_no_escalation_client_configured(seeded_pipeline):
    seeded_pipeline.llm = FakeLLM(["Partial [1].\nINSUFFICIENT_SOURCES"])
    seeded_pipeline.escalation_llm = None

    result = seeded_pipeline.query("Do I need a data protection officer?")
    assert not result.escalated
    assert result.insufficient
