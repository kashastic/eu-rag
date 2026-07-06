import pytest

from core.security import pii


def test_detects_email_phone_iban_card():
    text = (
        "Reach me at jane.doe@example.com or +49 30 12345678. "
        "IBAN DE89 3704 0044 0532 0130 00, card 4111 1111 1111 1111."
    )
    kinds = {f.kind for f in pii.scan(text)}
    assert {"EMAIL", "PHONE", "IBAN", "CARD"} <= kinds


def test_invalid_card_number_ignored():
    # fails the Luhn check → not a real card
    assert not any(f.kind == "CARD" for f in pii.scan("number 1234 5678 9012 3456"))


def test_findings_are_masked_never_full_value():
    findings = pii.scan("contact jane.doe@example.com")
    email = next(f for f in findings if f.kind == "EMAIL")
    assert "jane.doe@example.com" not in email.masked
    assert "…" in email.masked


def test_clean_text_has_no_findings():
    assert pii.scan("The controller shall designate a data protection officer.") == []


def test_gate_rejects_upload_with_pii():
    with pytest.raises(pii.PIIError) as exc:
        pii.gate("Signed by John, john@corp.eu", "upload")
    assert "EMAIL" in str(exc.value)


def test_gate_exempts_official_sources():
    # a regulation may legitimately contain an example email; not user data
    for source in ("eur-lex", "ec-portal", "national-scheme", "funding-calls"):
        pii.gate("see contact@agency.eu for details", source)  # must not raise


def test_gate_passes_clean_upload():
    pii.gate("Our returns policy allows 30 days.", "upload")  # must not raise
