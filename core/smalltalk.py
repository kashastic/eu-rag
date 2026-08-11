"""Conversational openers that are not questions about the corpus.

"hello", "thanks", "who are you" are not compliance questions, but retrieval
has no way to say so: BM25 and vector search *rank*, they never reject, so a
greeting still comes back with six passages of EU law and the answerer is then
told to answer the question using only those sources. What it produced was a
plausible-looking non-sequitur about whatever the corpus happened to rank
first — and it cost a HyDE call, a Sonnet call, sometimes an Opus escalation,
and one of the visitor's two free anonymous questions.

This module is the cheap half of the fix: a *deterministic* match, run before
contextualisation and before retrieval, that answers a greeting the way a
person would and spends nothing. The expensive half is the relevance floor in
`core.pipeline` — that one catches off-corpus questions and gibberish, which
have no fixed vocabulary to match against.

**Conservative on purpose: matching is on the WHOLE normalised string.** A
question is only smalltalk if there is nothing else in it, so "hi, do I need a
DPO?" is a real question and goes to retrieval untouched. A false positive here
is much worse than a false negative — a missed greeting costs a few cents,
whereas a real question answered with the canned reply looks like the product
is broken. That asymmetry is why there is no substring, keyword, or
model-based matching in here and should not be.

The reply is English regardless of the language matched. Every phrase in
PATTERNS is one a non-English speaker would still recognise as a greeting, the
UI around it is English, and the alternative — a model call to translate a
fixed string — would give back exactly the cost this function exists to avoid.
"""

import re
import unicodedata

# Whole-string patterns, normalised (see `_normalise`). Grouped by what they
# are, because the reply is the same but the reasoning about false positives is
# not: greetings are safe to match, "how does this work" is safe because the
# reply answers it, and thanks/farewells are safe because they cannot be the
# whole of a real question.
PATTERNS: frozenset[str] = frozenset(
    {
        # greetings, including the ones an EU visitor may type in their own
        # language — all recognisable, none of them a compliance question
        "hi", "hii", "hiii", "hey", "heya", "hello", "helo", "hallo", "yo",
        "hi there", "hey there", "hello there", "good morning",
        "good afternoon", "good evening", "good day", "greetings",
        "bonjour", "salut", "hola", "buenos dias", "ciao", "guten tag",
        "hallo zusammen", "ola", "czesc", "dzien dobry", "hej", "hei",
        "moi", "salve", "szia", "ahoj", "zdravo", "merhaba",
        # how-are-you
        "how are you", "how are you doing", "how are you today",
        "how do you do", "how is it going", "hows it going", "how are things",
        "you ok", "are you ok", "are you there", "you there",
        "whats up", "wassup", "sup",
        # identity / capability — the canned reply is a direct answer to these
        "who are you", "what are you", "what is this", "whats this",
        "what is eurag", "what's eurag", "who is eurag", "what do you do",
        "what can you do", "what can i ask", "what can i ask you",
        "what can you help with", "what can you help me with",
        "how does this work", "how do you work", "how does it work",
        "what is this for", "help", "help me", "start", "test", "testing",
        # thanks / farewell
        "thanks", "thank you", "thanks a lot", "thank you very much", "thx",
        "ty", "cheers", "nice", "cool", "great", "ok", "okay", "k",
        "bye", "goodbye", "good bye", "see you", "see ya", "later",
        "danke", "merci", "gracias", "grazie", "obrigado", "dziekuje",
    }
)

# Longest pattern above is ~24 chars. The cap is a second, independent guard:
# even if a pattern were ever added carelessly, nothing long enough to be a
# real question can reach the canned reply.
MAX_SMALLTALK_CHARS = 40

REPLY = (
    "Hello — I'm EURAG. I answer questions about EU compliance and funding for "
    "small and medium businesses, and every claim I make cites the official "
    "text it comes from.\n\n"
    "Ask me something specific and I'll show you the sources. For example:\n\n"
    "- Do I need a data protection officer for a 30-person company?\n"
    "- How long is the legal guarantee when I sell goods to consumers?\n"
    "- Which currently open EU funding calls could my startup apply to?\n"
    "- What interest can I charge when a business customer pays late?\n\n"
    "I can only answer from the official texts in my corpus, so if something "
    "falls outside it I'll tell you that rather than guess."
)

# Trailing politeness that can ride along without changing what the message is:
# "hello!" and "hi :)" are still greetings. Stripped before matching so they do
# not each need their own entry.
_TRIM = re.compile(r"^[\s!?.,;:'\"()\[\]\-–—*_]+|[\s!?.,;:'\"()\[\]\-–—*_]+$")
_SPACES = re.compile(r"\s+")


def _normalise(text: str) -> str:
    """Lowercase, strip emoji and edge punctuation, collapse whitespace.

    Accents are folded (`dzień` -> `dzien`, `ça` -> `ca`) so one entry covers
    the accented and unaccented spellings of the same word — a visitor typing
    on an English keyboard writes the second.
    """
    folded = unicodedata.normalize("NFKD", text.casefold())
    # drop combining marks (accents), symbols/emoji (S*), and the zero-width
    # joiners inside multi-codepoint emoji (C*) — the last of those is why
    # "hi 👨‍👩‍👦" would otherwise fail to normalise down to "hi"
    stripped = "".join(
        c for c in folded
        if not unicodedata.combining(c)
        and unicodedata.category(c)[0] not in ("S", "C")
    )
    return _SPACES.sub(" ", _TRIM.sub("", stripped)).strip()


def is_smalltalk(question: str) -> bool:
    """True when the message is *only* a greeting, thanks, or "what is this".

    Whole-string match: any real content alongside the greeting means the
    message is a question and this returns False.
    """
    if len(question) > MAX_SMALLTALK_CHARS:
        return False
    return _normalise(question) in PATTERNS
