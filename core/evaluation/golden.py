"""Golden questions — the single source of truth for retrieval evaluation.

Consumed by tests/evaluation/ (pass/fail bar: document-level hit) and by
core.evaluation.harness (measurement: document- and chunk-level metrics).

Each case pins the document that must be retrieved (doc_marker, substring of
the canonical title) and, where meaningful, verbatim phrases from the passage
that actually answers the question (chunk-level precision — the reranker's
job). Phrases are corpus-coupled on purpose: they were checked against the
ingested texts and must be re-checked if a source is re-pulled.

Cases with core=False cover documents that only exist once the EUR-Lex pull
has run (python -m data.scrapers.eurlex); consumers skip them when the
document is absent instead of failing a fresh clone.
"""

from dataclasses import dataclass


def marker_present(marker: str, titles: list[str]) -> bool:
    """True when any alternative ("A|B") of the marker appears in a title."""
    return any(
        alt.strip().lower() in title
        for alt in marker.split("|")
        for title in titles
    )


@dataclass(frozen=True)
class GoldenCase:
    question: str
    doc_marker: str  # substring expected in the retrieved document title
    phrases: tuple[str, ...] = ()  # verbatim, case-insensitive; any-of
    core: bool = True  # False → skip when the document is not in the corpus
    # compound questions: ALL these title markers must appear in the top-k
    # (measures multi-hop / decomposition quality)
    requires_all: tuple[str, ...] = ()
    # prior (question, answer) turns, oldest first. Non-empty means this is a
    # FOLLOW-UP: the question is deliberately not self-contained, and retrieval
    # can only succeed if it is first rewritten against these turns. Answers
    # are abridged — only their topic is load-bearing.
    history: tuple[tuple[str, str], ...] = ()


CASES: list[GoldenCase] = [
    # --- core: answerable on a fresh clone (samples) and after the pull ----
    GoldenCase(
        "Do I need a data protection officer for a 30 person company?",
        "GDPR",
        ("shall designate a data protection officer",),
    ),
    GoldenCase(
        "When must I notify the authority about a data breach?",
        "GDPR",
        ("not later than 72 hours",),
    ),
    GoldenCase(
        "Which lawful bases for processing personal data exist?",
        "GDPR",
        ("processing shall be lawful only if",),
    ),
    GoldenCase(
        "Does my 40-employee company count as a small enterprise?",
        "SME definition",
        ("fewer than 50 persons",),
    ),
    GoldenCase(
        "What are the turnover thresholds for an SME?",
        "SME definition",
        ("EUR 50 million",),
    ),
    GoldenCase(
        "How much grant money can the EIC Accelerator provide?",
        "EU funding",
        ("accelerator",),
    ),
    GoldenCase(
        "What is the Enterprise Europe Network?",
        # sample overview on a fresh clone; real EC portal pages once pulled
        "EU funding|EC portal",
        ("enterprise europe network",),
    ),
    GoldenCase(
        "What KfW loan exists for young German companies?",
        "KfW",
        ("kfw",),
    ),
    GoldenCase(
        "Quels prêts Bpifrance propose-t-elle aux PME ?",
        "KfW",  # the national-schemes doc title names both KfW and Bpifrance
        ("bpifrance",),
    ),
    # --- extended: EUR-Lex pull corpus only --------------------------------
    GoldenCase(
        "Which AI systems are classified as high-risk?",
        "AI Act",
        ("classified as high-risk", "considered to be high-risk"),
        core=False,
    ),
    GoldenCase(
        "What interest can I charge when a business customer pays late?",
        "Late Payment",
        ("statutory interest",),
        core=False,
    ),
    GoldenCase(
        "How long does a consumer have to withdraw from an online purchase?",
        "Consumer Rights",
        ("period of 14 days",),
        core=False,
    ),
    GoldenCase(
        "Which large platforms are designated as gatekeepers?",
        "Digital Markets Act",
        ("designate as gatekeeper", "designated as gatekeeper"),
        core=False,
    ),
    GoldenCase(
        "Must my company report significant incidents under NIS2?",
        "NIS2",
        ("significant incident",),
        core=False,
    ),
    GoldenCase(
        "Which companies must include sustainability reporting in their management report?",
        "Sustainability Reporting",
        ("sustainability reporting",),
        core=False,
    ),
    GoldenCase(
        "Are persons who report breaches of Union law protected against retaliation?",
        "Whistleblower",
        ("retaliation",),
        core=False,
    ),
    GoldenCase(
        "What safety obligations do online marketplaces have for products?",
        "Product Safety",
        ("online marketplace",),
        core=False,
    ),
    # --- second EUR-Lex wave, 2026-07-06 ------------------------------------
    GoldenCase(
        "Do I have cybersecurity obligations when selling connected products?",
        "Cyber Resilience",
        ("products with digital elements",),
        core=False,
    ),
    GoldenCase(
        "How long is the legal guarantee when I sell goods to consumers?",
        "Sale of Goods",
        ("two years",),
        core=False,
    ),
    GoldenCase(
        "What is the maximum average weekly working time for my employees?",
        "Working Time",
        ("48 hours",),
        core=False,
    ),
    GoldenCase(
        "Can a small business be exempt from charging VAT?",
        "VAT Small",
        ("exemption",),
        core=False,
    ),
    GoldenCase(
        "Must I share pay range information with job applicants?",
        "Pay Transparency",
        # Art. 5 (applicants) or Art. 7 (employees) both answer this
        ("initial pay or its range", "right to information"),
        core=False,
    ),
    # --- Tier 3: funding portals (2026-07-06) -------------------------------
    GoldenCase(
        "How can an SME get EU funding or access to finance?",
        "EC portal",
        ("access to finance",),
        core=False,
    ),
    GoldenCase(
        "Which currently open EU funding calls are relevant for SMEs?",
        "Funding & Tenders",
        ("deadline",),
        core=False,
    ),
    GoldenCase(
        "What loans does Almi offer to Swedish companies?",
        "Almi",
        ("loans",),
        core=False,
    ),
    GoldenCase(
        "Which Dutch agency helps entrepreneurs with subsidies and financing?",
        "RVO",
        ("netherlands enterprise agency",),
        core=False,
    ),
    # --- compound questions (multi-hop; decomposition target) ---------------
    GoldenCase(
        "If I sell software online, how long is the consumer guarantee and"
        " within how many days can customers withdraw?",
        "Consumer Rights",
        ("period of 14 days",),
        core=False,
        requires_all=("Consumer Rights", "Digital Content"),
    ),
    GoldenCase(
        "Do I have to notify a personal data breach and also report"
        " significant incidents under NIS2?",
        "GDPR",
        ("not later than 72 hours",),
        core=False,
        requires_all=("GDPR", "NIS2"),
    ),
    GoldenCase(
        "Can I refuse customers from another EU country and can I charge"
        " them a different VAT rate?",
        "Geo-blocking",
        (),
        core=False,
        requires_all=("Geo-blocking", "VAT"),
    ),
    # --- follow-ups: the question is NOT self-contained on purpose ---------
    # Each fails without contextualisation, and the failure is not random:
    # stripped of its conversation, the question's only lexical signal points
    # at a different act (a headcount matches Pay Transparency's "fewer than
    # 100 workers"; a bare "in France" matches nothing at all). Observed live
    # 2026-08-09 — see docs/UPDATE_LOG.md.
    GoldenCase(
        "what if I have 29 people?",
        "GDPR",
        ("shall designate a data protection officer",),
        core=False,
        history=(
            (
                "Do I need a data protection officer for a 30-person company?",
                "Whether you must appoint a DPO does not depend on headcount."
                " Under Article 37(1) GDPR it depends on whether you are a"
                " public authority, or your core activities involve regular"
                " and systematic monitoring on a large scale, or large-scale"
                " processing of special categories of data.",
            ),
        ),
    ),
    GoldenCase(
        "and how long do I have to report one?",
        "GDPR",
        ("not later than 72 hours",),
        core=False,
        history=(
            (
                "What counts as a personal data breach under the GDPR?",
                "A personal data breach is a breach of security leading to the"
                " accidental or unlawful destruction, loss, alteration,"
                " unauthorised disclosure of, or access to, personal data.",
            ),
        ),
    ),
    GoldenCase(
        "what about the withdrawal period?",
        "Consumer Rights",
        ("period of 14 days",),
        core=False,
        history=(
            (
                "What are my obligations when selling to consumers online?",
                "Distance selling to consumers triggers pre-contractual"
                " information duties and a right of withdrawal under the"
                " Consumer Rights Directive.",
            ),
        ),
    ),
]
