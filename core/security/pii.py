"""PII gate: scans documents BEFORE they are chunked or embedded.

Applies to user uploads only — the official sources (EUR-Lex, EC portal,
national agencies, funding calls) are public texts pulled by our own
verified scrapers and are exempt. On detection the document is REJECTED with
the finding types, not silently redacted: redaction could corrupt a legal
text, and the uploader is the right party to clean their document.

Default backend is a regex/checksum scanner (emails, international phone
numbers, IBANs, Luhn-valid card numbers) with no model downloads. Set
EURAG_PII_BACKEND=presidio to use Microsoft Presidio when installed
(pip install presidio-analyzer, plus a spaCy model) for NER-based detection
of names and addresses.
"""

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# source types produced by our own scrapers from public official pages
OFFICIAL_SOURCE_TYPES = {
    "eur-lex",
    "ec-portal",
    "national-scheme",
    "funding-calls",
    "curated",
}

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")
_PHONE_RE = re.compile(r"(?<![\w/.])\+\d{1,3}[ .-]?(?:\(?\d{1,4}\)?[ .-]?)?\d{2,4}[ .-]?\d{2,4}(?:[ .-]?\d{2,4})?\b")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


class PIIError(ValueError):
    """Document contains personal data and must not enter the corpus."""

    def __init__(self, findings: list["Finding"]):
        self.findings = findings
        kinds = ", ".join(sorted({f.kind for f in findings}))
        super().__init__(
            f"document rejected — possible personal data found ({kinds}). "
            "Remove or anonymise it and ingest again."
        )


@dataclass(frozen=True)
class Finding:
    kind: str  # EMAIL | PHONE | IBAN | CARD | <presidio entity>
    masked: str  # first/last chars only — never the full value


def _mask(value: str) -> str:
    flat = value.strip()
    return flat[:2] + "…" + flat[-2:] if len(flat) > 6 else "…"


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _regex_scan(text: str) -> list[Finding]:
    findings: list[Finding] = []
    for match in _EMAIL_RE.finditer(text):
        findings.append(Finding("EMAIL", _mask(match.group())))
    for match in _PHONE_RE.finditer(text):
        findings.append(Finding("PHONE", _mask(match.group())))
    for match in _IBAN_RE.finditer(text):
        findings.append(Finding("IBAN", _mask(match.group())))
    for match in _CARD_RE.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            findings.append(Finding("CARD", _mask(digits)))
    return findings


def _presidio_scan(text: str) -> list[Finding]:
    from presidio_analyzer import AnalyzerEngine  # optional dependency

    analyzer = AnalyzerEngine()
    results = analyzer.analyze(text=text, language="en")
    return [
        Finding(r.entity_type, _mask(text[r.start : r.end]))
        for r in results
        if r.score >= 0.5
    ]


def scan(text: str, backend: str = "regex") -> list[Finding]:
    if backend == "presidio":
        try:
            return _presidio_scan(text)
        except ImportError:
            logger.warning(
                "presidio backend requested but not installed — regex scan used"
            )
    return _regex_scan(text)


def gate(text: str, source_type: str, *, backend: str = "regex") -> None:
    """Raises PIIError when an upload contains personal data."""
    if source_type in OFFICIAL_SOURCE_TYPES:
        return
    findings = scan(text, backend=backend)
    if findings:
        raise PIIError(findings)
