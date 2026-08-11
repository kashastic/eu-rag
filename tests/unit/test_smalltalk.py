"""Greetings are answered without retrieving or generating anything.

The bug: retrieval ranks, it never rejects, so "hello" came back with six
passages of EU law and the answerer was told to answer from them. Measured on
the live pipeline, "hello" cost a HyDE call, two Sonnet attempts, an Opus
escalation and two more attempts, and shipped three verbatim quotes from the
Pay Transparency Directive as the answer.

Two properties matter and both are pinned here: the canned reply fires for an
actual greeting, and it does NOT fire for anything carrying a real question —
a false positive answers a paying question with a form letter, which is far
worse than the cost of a missed greeting.
"""

import logging

from core.smalltalk import REPLY, is_smalltalk


# --- the classifier ---------------------------------------------------------


def test_bare_greetings_match():
    for text in (
        "hello", "Hi!", "HEY", "  hey there  ", "good morning",
        "how are you doing?", "how are you", "thanks!", "thank you very much",
        "who are you", "What is this?", "what can you do", "how does this work?",
        "bye", "ok", "sup",
    ):
        assert is_smalltalk(text), text


def test_greetings_in_other_eu_languages_match():
    # a Polish or Spanish visitor typing a greeting should not pay for an
    # answer either, and these are recognisable without a model call
    for text in ("Bonjour", "hola", "Ciao", "guten tag", "dzień dobry", "merci"):
        assert is_smalltalk(text), text


def test_accents_and_emoji_do_not_defeat_the_match():
    assert is_smalltalk("dzien dobry")  # unaccented, as typed on a UK keyboard
    assert is_smalltalk("hi 👋")
    assert is_smalltalk("hello!!!")


def test_a_greeting_carrying_a_real_question_is_not_smalltalk():
    """The whole-string rule, which is the entire safety argument.

    Anything alongside the greeting means the message is a question, and a
    question must reach retrieval however politely it is phrased.
    """
    for text in (
        "hi, do I need a DPO?",
        "hello, what is the GDPR fine cap",
        "thanks — but what about late payment interest?",
        "what is this regulation about for my company",
        "who are you required to notify after a data breach",
        "how does this work for a 30-person company",
    ):
        assert not is_smalltalk(text), text


def test_off_corpus_questions_are_not_smalltalk():
    """Not everything unanswerable is a greeting.

    "blah blah" and "what is the weather" go down the normal path and get an
    honest, model-written refusal — which is measured to be a good answer. They
    are not in scope for a canned reply, and matching them by pattern would be
    an endless list.
    """
    for text in ("blah blah", "what is the weather in Berlin", "tell me a joke"):
        assert not is_smalltalk(text)


def test_long_input_is_never_smalltalk():
    # the length cap is a second, independent guard on the pattern list
    assert not is_smalltalk("hello " * 20)


# --- the pipeline short-circuit ---------------------------------------------


class _CountingLLM:
    name = "counting"

    def __init__(self):
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        return "should never be called [1]"


def _outcome_lines(caplog):
    return [r.message for r in caplog.records if r.message.startswith("query outcome:")]


def test_smalltalk_answers_without_calling_a_model(seeded_pipeline):
    llm = _CountingLLM()
    seeded_pipeline.llm = llm
    seeded_pipeline.escalation_llm = _CountingLLM()

    result = seeded_pipeline.query("hello")

    assert result.answer == REPLY
    assert result.mode == "smalltalk"
    assert llm.calls == 0
    assert seeded_pipeline.escalation_llm.calls == 0


def test_smalltalk_does_not_retrieve(seeded_pipeline, monkeypatch):
    """Cheap is the point, but so is correctness: retrieving for a greeting is
    what produced citations to the Pay Transparency Directive."""
    def _boom(*a, **kw):
        raise AssertionError("retrieval must not run for smalltalk")

    monkeypatch.setattr(seeded_pipeline.retriever, "retrieve_scored", _boom)

    assert seeded_pipeline.query("hi there").mode == "smalltalk"


def test_smalltalk_is_not_flagged_insufficient(seeded_pipeline):
    """It is a complete answer to what was asked, not a failure to answer.

    `insufficient` drives the "sources incomplete" badge and the escalation
    gate, so marking a greeting insufficient would both mislabel it in the UI
    and hand it to Opus.
    """
    result = seeded_pipeline.query("thanks")

    assert result.insufficient is False
    assert result.escalated is False
    assert result.citations == []


def test_smalltalk_runs_before_contextualisation(seeded_pipeline):
    """Order matters: a fragment like "thanks" at the end of a thread would
    otherwise be rewritten into a full standalone question and then answered
    at random — which is the reported bug wearing a different hat. It also
    saves the Haiku call the rewrite would have cost."""
    class _Contextualizer:
        def __init__(self):
            self.calls = 0

        def standalone(self, question, history):
            self.calls += 1
            return "a rewritten question about data protection officers"

    ctx = _Contextualizer()
    seeded_pipeline.contextualizer = ctx

    result = seeded_pipeline.query(
        "thanks", history=[("Do I need a DPO?", "Yes, in some cases [1].")]
    )

    assert result.mode == "smalltalk"
    assert ctx.calls == 0


def test_smalltalk_still_logs_exactly_one_outcome_line(seeded_pipeline, caplog):
    """`query outcome:` is the denominator for the escalation rate. A visitor
    typing "hello" is traffic; dropping it from the count would overstate the
    rate of everything measured against it."""
    with caplog.at_level(logging.INFO, logger="core.pipeline"):
        seeded_pipeline.query("hello")

    lines = _outcome_lines(caplog)
    assert len(lines) == 1
    assert "mode=smalltalk" in lines[0]
    assert "escalated=False" in lines[0]
    assert lines[0].isascii()  # counted with grep, like the rest of the line


def test_a_real_question_is_unaffected(seeded_pipeline):
    llm = _CountingLLM()
    seeded_pipeline.llm = llm
    seeded_pipeline.escalation_llm = None

    result = seeded_pipeline.query("What are the SME turnover thresholds?")

    assert result.mode != "smalltalk"
    assert llm.calls >= 1
