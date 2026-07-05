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


@dataclass(frozen=True)
class GoldenCase:
    question: str
    doc_marker: str  # substring expected in the retrieved document title
    phrases: tuple[str, ...] = ()  # verbatim, case-insensitive; any-of
    core: bool = True  # False → skip when the document is not in the corpus


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
        "EU funding",
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
        ("right to information",),
        core=False,
    ),
]
