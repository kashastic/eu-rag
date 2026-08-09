"""One outcome line per query, with the cause of any escalation.

The escalation rate was unmeasurable before this: the only per-query log lines
fired on a branch, so escalations had no denominator to divide by. These tests
pin the properties that make the line countable — it is emitted for EVERY
query, and it distinguishes the two very different reasons a query escalates.
"""

import logging

from core.generation.answerer import answer_question
from core.generation.llm_client import ExtractiveClient
from core.ingestion.chunker import Chunk


class FakeLLM:
    name = "fake"

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        # repeat the last response once exhausted: the answerer retries a
        # failed generation, and these tests care about the outcome, not the
        # retry count
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


def _chunks():
    return [
        Chunk(
            chunk_id="c1",
            doc_id="d1",
            index=0,
            text="Article 37 - the controller shall designate a data protection officer.",
            title="GDPR",
            source_url="https://example.eu/gdpr",
        )
    ]


def _outcome_lines(caplog):
    return [r.message for r in caplog.records if r.message.startswith("query outcome:")]


# --- the reason field, at the answerer level ---------------------------------


def test_marker_insufficiency_is_labelled_marker():
    llm = FakeLLM(["Partial answer [1].\nINSUFFICIENT_SOURCES"])
    result = answer_question("Do I need a DPO?", _chunks(), llm)
    assert result.insufficient
    assert result.insufficient_reason == "marker"


def test_uncited_fallback_is_labelled_uncited():
    # a fabricated marker fails validation twice and falls back. (An honest
    # *uncited* refusal no longer lands here — see test_answerer.py.)
    llm = FakeLLM(["Fabricated citation [9]."])
    result = answer_question("What is the VAT rate in Portugal?", _chunks(), llm)
    assert result.insufficient
    assert result.mode == "extractive"
    assert result.insufficient_reason == "uncited"


def test_empty_retrieval_is_labelled_no_sources():
    result = answer_question("Anything?", [], FakeLLM(["unused"]))
    assert result.insufficient_reason == "no_sources"


def test_confident_answer_has_no_reason():
    llm = FakeLLM(["Fully answered from the sources [1]."])
    result = answer_question("Do I need a DPO?", _chunks(), llm)
    assert not result.insufficient
    assert result.insufficient_reason is None


def test_reason_is_not_exposed_in_the_api_payload():
    # internal diagnostic; the response shape is a published contract
    llm = FakeLLM(["Partial [1].\nINSUFFICIENT_SOURCES"])
    result = answer_question("Do I need a DPO?", _chunks(), llm)
    assert "insufficient_reason" not in result.to_dict()


# --- the outcome line, at the pipeline level ---------------------------------


def test_every_query_logs_exactly_one_outcome_line(seeded_pipeline, caplog):
    seeded_pipeline.llm = FakeLLM(["Fully answered [1]."])
    seeded_pipeline.escalation_llm = None

    with caplog.at_level(logging.INFO, logger="core.pipeline"):
        seeded_pipeline.query("What are the SME turnover thresholds?")

    assert len(_outcome_lines(caplog)) == 1


def test_unescalated_query_logs_no_trigger(seeded_pipeline, caplog):
    seeded_pipeline.llm = FakeLLM(["Fully answered [1]."])
    seeded_pipeline.escalation_llm = FakeLLM(["unused"])

    with caplog.at_level(logging.INFO, logger="core.pipeline"):
        seeded_pipeline.query("What are the SME turnover thresholds?")

    line = _outcome_lines(caplog)[0]
    assert "escalated=False" in line
    assert "primary_reason=none" in line


def test_escalation_records_the_reason_that_caused_it(seeded_pipeline, caplog):
    seeded_pipeline.llm = FakeLLM(["Partial [1].\nINSUFFICIENT_SOURCES"])
    seeded_pipeline.escalation_llm = FakeLLM(["Complete escalated answer [1]."])

    with caplog.at_level(logging.INFO, logger="core.pipeline"):
        result = seeded_pipeline.query("Do I need a data protection officer?")

    assert result.escalated
    line = _outcome_lines(caplog)[0]
    assert "escalated=True" in line
    # the escalated answer succeeded, but the line still reports WHY we paid
    assert "primary_reason=marker" in line
    assert "insufficient=False" in line


def test_honest_refusal_costs_one_call_per_stage_and_ships_the_refusal(
    seeded_pipeline, caplog
):
    """The path this instrument was built to quantify, after the fix.

    An honest zero-citation refusal used to fail citation validation, retry,
    fail again, downgrade to extractive, and only then escalate — 2 primary +
    2 escalation calls, ending in verbatim quotes from the chunks the model
    had just refused to use. It now costs one call per stage and the user
    sees the refusal itself.

    The escalation still fires, and should: the model reporting a corpus gap
    is exactly what deeper retrieval exists to rescue. What changed is the
    price of finding out, and what ships when the rescue also fails.
    """
    refusal = "The sources provided do not address this.\nINSUFFICIENT_SOURCES"
    primary = FakeLLM([refusal])
    escalation = FakeLLM([refusal])
    seeded_pipeline.llm = primary
    seeded_pipeline.escalation_llm = escalation

    with caplog.at_level(logging.INFO, logger="core.pipeline"):
        result = seeded_pipeline.query("What is the VAT rate on software in Portugal?")

    assert primary.calls == 1  # was 2
    assert escalation.calls == 1  # was 2
    assert result.mode == "llm"  # was "extractive"
    assert result.answer == "The sources provided do not address this."
    assert result.insufficient  # honesty survives escalation
    assert result.citations == []

    line = _outcome_lines(caplog)[0]
    assert "escalated=True" in line
    assert "primary_reason=marker" in line  # a corpus gap, not a format failure


def test_outcome_line_is_ascii_greppable(seeded_pipeline, caplog):
    # a grep for the escalation log failed once on its em dash; this line is
    # the one people will count, so it must survive a naive grep
    seeded_pipeline.llm = FakeLLM(["Fully answered [1]."])
    seeded_pipeline.escalation_llm = None

    with caplog.at_level(logging.INFO, logger="core.pipeline"):
        seeded_pipeline.query("What are the SME turnover thresholds?")

    line = _outcome_lines(caplog)[0]
    assert line.isascii()


def test_extractive_client_still_logs_an_outcome(seeded_pipeline, caplog):
    # no API key at all — the denominator must still count these
    seeded_pipeline.llm = ExtractiveClient()
    seeded_pipeline.escalation_llm = None

    with caplog.at_level(logging.INFO, logger="core.pipeline"):
        seeded_pipeline.query("Do I need a data protection officer?")

    assert len(_outcome_lines(caplog)) == 1
