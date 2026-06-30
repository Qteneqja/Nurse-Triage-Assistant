"""Gate: the email-probing question is allowed for non-clinical verticals that
store PHI (e.g. Birchwood booking confirmations) but stays blocked in a clinical
context; SSN / credit-card / address probing stays blocked everywhere.
"""

from __future__ import annotations

from src.safety.gate import GateContext, gate_outbound_text


def _ctx(store_phi: bool) -> GateContext:
    return GateContext(session_id="gate-email-test", store_phi=store_phi)


def test_email_question_allowed_when_storing_phi():
    gated = gate_outbound_text(
        "Sure - what's your email address for the booking confirmation?",
        _ctx(store_phi=True),
        "question",
    )
    assert "email" in gated.lower()
    assert "removed for privacy" not in gated


def test_email_question_blocked_in_clinical_context():
    gated = gate_outbound_text(
        "Can I get your email address?", _ctx(store_phi=False), "question"
    )
    assert "removed for privacy" in gated


def test_ssn_blocked_even_when_storing_phi():
    gated = gate_outbound_text(
        "What's your social security number?", _ctx(store_phi=True), "question"
    )
    assert "removed for privacy" in gated


def test_address_blocked_even_when_storing_phi():
    gated = gate_outbound_text(
        "And what's your address?", _ctx(store_phi=True), "question"
    )
    assert "removed for privacy" in gated


def test_credit_card_blocked_even_when_storing_phi():
    gated = gate_outbound_text(
        "What's your credit card number?", _ctx(store_phi=True), "question"
    )
    assert "removed for privacy" in gated
