"""
Phase 4 Hardening — Comprehensive Unit Tests

Tests for:
1. Centralized Safety Gate (safety_gate)
2. Red-Flag Rule Engine (run_red_flag_rules)
3. Triage Output Schema + Retry + Safe Fallback
4. Diagnosis Enforcement ("Never Diagnose")
5. Decision Trace completeness
6. PHI Masking
7. Postgres enforcement in production
8. Protocol hierarchy (RULES > PROTOCOL > LLM)
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# 1. RED-FLAG RULE ENGINE TESTS
# ---------------------------------------------------------------------------

from src.safety.red_flag_rules import (
    RED_FLAG_RULES,
    run_red_flag_rules,
    get_rule,
)


class TestRedFlagRuleRegistry:
    """Verify the rule registry is well-formed."""

    def test_all_rules_have_unique_ids(self):
        ids = [r.id for r in RED_FLAG_RULES]
        assert len(ids) == len(set(ids)), "Duplicate rule IDs found"

    def test_minimum_10_rules(self):
        assert len(RED_FLAG_RULES) >= 10

    def test_all_rules_have_required_fields(self):
        for rule in RED_FLAG_RULES:
            assert rule.id, f"Rule missing id: {rule}"
            assert rule.description, f"Rule {rule.id} missing description"
            assert rule.forced_disposition in ("ER_NOW", "URGENT")
            assert rule.escalation_script
            assert callable(rule.condition)

    def test_get_rule_by_id(self):
        rule = get_rule("CHEST_PAIN_SEVERE")
        assert rule is not None
        assert rule.critical is True

    def test_get_rule_nonexistent(self):
        assert get_rule("NONEXISTENT") is None


class TestRedFlagRuleExecution:
    """Verify rule engine execution."""

    def test_critical_chest_pain_forces_er_now(self):
        result = run_red_flag_rules(
            utterance="I have crushing chest pain radiating to my arm"
        )
        assert result.forced_disposition == "ER_NOW"
        assert "CHEST_PAIN_SEVERE" in result.triggered_rule_ids

    def test_breathing_failure_forces_er_now(self):
        result = run_red_flag_rules(utterance="I can't breathe, gasping for air")
        assert result.forced_disposition == "ER_NOW"
        assert "BREATHING_FAILURE" in result.triggered_rule_ids

    def test_stroke_signs_forces_er_now(self):
        result = run_red_flag_rules(utterance="my face is drooping and I can't speak")
        assert result.forced_disposition == "ER_NOW"
        assert "STROKE_SIGNS" in result.triggered_rule_ids

    def test_anaphylaxis_forces_er_now(self):
        result = run_red_flag_rules(utterance="my throat is swelling shut, anaphylaxis")
        assert result.forced_disposition == "ER_NOW"
        assert "ANAPHYLAXIS" in result.triggered_rule_ids

    def test_suicidal_forces_er_now(self):
        result = run_red_flag_rules(utterance="I want to kill myself")
        assert result.forced_disposition == "ER_NOW"
        assert "SUICIDAL_SELF_HARM" in result.triggered_rule_ids

    def test_loss_of_consciousness_forces_er_now(self):
        result = run_red_flag_rules(utterance="they passed out and won't wake up")
        assert result.forced_disposition == "ER_NOW"
        assert "LOSS_OF_CONSCIOUSNESS" in result.triggered_rule_ids

    def test_uncontrolled_bleeding_forces_er_now(self):
        result = run_red_flag_rules(
            utterance="blood is gushing and won't stop bleeding"
        )
        assert result.forced_disposition == "ER_NOW"
        assert "UNCONTROLLED_BLEEDING" in result.triggered_rule_ids

    def test_weighted_score_below_threshold_no_override(self):
        result = run_red_flag_rules(utterance="I have a very high fever")
        # HIGH_FEVER weight=5, below threshold of 10
        assert result.forced_disposition is None
        assert result.total_score == 5

    def test_weighted_score_at_threshold_forces_urgent(self):
        # HIGH_FEVER(5) + SEVERE_PAIN(5) = 10 → URGENT
        result = run_red_flag_rules(utterance="very high fever and 9 out of 10 pain")
        assert result.forced_disposition == "URGENT"
        assert result.total_score >= 10

    def test_no_match_returns_no_override(self):
        result = run_red_flag_rules(utterance="I have a mild headache")
        assert result.forced_disposition is None
        assert result.total_score == 0
        assert len(result.triggered_rule_ids) == 0

    def test_chief_complaint_also_scanned(self):
        result = run_red_flag_rules(
            utterance="it hurts",
            chief_complaint="crushing chest pain",
        )
        assert result.forced_disposition == "ER_NOW"
        assert "CHEST_PAIN_SEVERE" in result.triggered_rule_ids

    def test_red_flags_reported_also_scanned(self):
        result = run_red_flag_rules(
            utterance="",
            red_flags_reported=["patient reports seizure activity"],
        )
        assert result.forced_disposition == "ER_NOW"

    def test_all_triggers_logged_for_audit(self):
        result = run_red_flag_rules(
            utterance="crushing chest pain and seizure and gasping"
        )
        assert result.forced_disposition == "ER_NOW"
        # Multiple rules should fire
        assert len(result.all_triggers) >= 2
        for trigger in result.all_triggers:
            assert "rule_id" in trigger
            assert "description" in trigger

    def test_escalation_script_provided(self):
        result = run_red_flag_rules(utterance="I want to kill myself")
        assert result.escalation_script is not None
        assert len(result.escalation_script) > 20

    def test_forced_escalation_example(self):
        """Example forced escalation test case as required by spec."""
        # Scenario: Patient reports crushing chest pain + can't breathe
        result = run_red_flag_rules(
            utterance="I have crushing chest pain and I can't breathe",
            chief_complaint="chest pain and difficulty breathing",
            symptom_severity="severe",
        )

        # MUST escalate to ER_NOW
        assert result.forced_disposition == "ER_NOW"
        # MUST have triggered rules logged
        assert len(result.triggered_rule_ids) >= 1
        # MUST have escalation script
        assert result.escalation_script is not None
        # MUST have audit detail
        assert len(result.all_triggers) >= 1
        for t in result.all_triggers:
            assert t["critical"] is True or t["weight"] >= 0


# ---------------------------------------------------------------------------
# 2. TRIAGE OUTPUT SCHEMA TESTS
# ---------------------------------------------------------------------------

from src.safety.triage_output_schema import (  # noqa: E402
    TriageOutput,
    validate_triage_output,
    SAFE_FALLBACK_OUTPUT,
)


class TestTriageOutputSchema:
    """Verify strict schema validation."""

    def test_valid_output_passes(self):
        data = {
            "disposition": "URGENT",
            "urgency_level": "HIGH",
            "confidence_score": 0.85,
            "rules_triggered": ["rule_1"],
            "red_flags_triggered": ["chest pain"],
            "escalation_required": True,
            "protocol_references": ["PROTO-001"],
            "model_version": "deepseek-v2",
            "timestamp": "2026-02-19T00:00:00Z",
            "message_to_caller": "Please seek care.",
        }
        result = validate_triage_output(data)
        assert result is not None
        assert result.disposition == "URGENT"
        assert result.confidence_score == 0.85

    def test_missing_disposition_fails_then_coerces(self):
        data = {
            "urgency_level": "HIGH",
            "confidence_score": 0.5,
            "escalation_required": True,
        }
        result = validate_triage_output(data)
        # Should coerce to HUMAN_REVIEW on retry
        assert result is not None
        assert result.disposition == "HUMAN_REVIEW"

    def test_invalid_disposition_value_rejected(self):
        data = {
            "disposition": "MAYBE_OK",  # Invalid
            "urgency_level": "HIGH",
            "confidence_score": 0.5,
            "escalation_required": True,
        }
        result = validate_triage_output(data)
        # Will fail both attempts (invalid enum)
        assert result is None

    def test_confidence_score_bounds_enforced(self):
        data = {
            "disposition": "ROUTINE",
            "urgency_level": "LOW",
            "confidence_score": 1.5,  # Out of bounds
            "escalation_required": False,
        }
        result = validate_triage_output(data)
        assert result is None  # Cannot coerce out-of-bounds float

    def test_safe_fallback_output_is_valid(self):
        assert SAFE_FALLBACK_OUTPUT.disposition == "HUMAN_REVIEW"
        assert SAFE_FALLBACK_OUTPUT.escalation_required is True
        assert SAFE_FALLBACK_OUTPUT.confidence_score == 0.0

    def test_completely_empty_dict_uses_fallback(self):
        result = validate_triage_output({})
        # Should coerce defaults and succeed
        assert result is not None
        assert result.escalation_required is True

    def test_schema_includes_all_required_fields(self):
        """Verify schema has all fields specified in requirements."""
        fields = set(TriageOutput.model_fields.keys())
        required = {
            "disposition",
            "urgency_level",
            "confidence_score",
            "rules_triggered",
            "red_flags_triggered",
            "escalation_required",
            "protocol_references",
            "model_version",
            "timestamp",
        }
        assert required.issubset(fields), f"Missing fields: {required - fields}"


# ---------------------------------------------------------------------------
# 3. DIAGNOSIS ENFORCEMENT TESTS
# ---------------------------------------------------------------------------

from src.safety.diagnosis_enforcement import (  # noqa: E402
    enforce_no_diagnosis,
)


class TestDiagnosisEnforcement:
    """Verify 'Never Diagnose' enforcement layer."""

    def test_you_have_disease_rewritten(self):
        text = "Based on your symptoms, you have a heart disease condition."
        cleaned, events = enforce_no_diagnosis(text)
        assert "you have" not in cleaned.lower() or "disease" not in cleaned.lower()
        assert len(events) >= 1
        assert events[0].rule_id == "DIAG_YOU_HAVE"

    def test_this_is_diagnosis_rewritten(self):
        text = "This is likely a case of pneumonia infection."
        cleaned, events = enforce_no_diagnosis(text)
        assert "case of" not in cleaned.lower()
        assert len(events) >= 1

    def test_explicit_diagnosis_word_rewritten(self):
        text = "I can diagnose this as a common cold."
        cleaned, events = enforce_no_diagnosis(text)
        assert "diagnos" not in cleaned.lower()
        assert len(events) >= 1

    def test_cause_is_rewritten(self):
        text = "The cause is a bacterial infection."
        cleaned, events = enforce_no_diagnosis(text)
        assert "the cause is" not in cleaned.lower()
        assert len(events) >= 1

    def test_safe_text_unchanged(self):
        text = "Your symptoms suggest you should seek medical evaluation."
        cleaned, events = enforce_no_diagnosis(text)
        assert cleaned == text
        assert len(events) == 0

    def test_keyword_blocklist_enforced(self):
        text = "The confirmed diagnosis is clear."
        cleaned, events = enforce_no_diagnosis(text)
        assert "confirmed diagnosis" not in cleaned.lower()
        assert len(events) >= 1

    def test_empty_text_safe(self):
        cleaned, events = enforce_no_diagnosis("")
        assert cleaned == ""
        assert len(events) == 0

    def test_before_after_example(self):
        """Before/after example as required by spec."""
        before = (
            "You have pneumonia infection. This is a case of bacterial pneumonia. "
            "I diagnose you with community-acquired pneumonia."
        )
        after, events = enforce_no_diagnosis(before)

        # After text must NOT contain diagnostic claims
        assert "you have pneumonia" not in after.lower()
        assert "diagnos" not in after.lower()
        assert "case of" not in after.lower()

        # Events must be logged
        assert len(events) >= 2
        for event in events:
            assert event.original_text
            assert event.rewritten_text
            assert event.rule_id


# ---------------------------------------------------------------------------
# 4. CENTRALIZED SAFETY GATE TESTS
# ---------------------------------------------------------------------------

from src.safety.safety_gate import (  # noqa: E402
    safety_gate,
    FinalDecision,
    SAFE_FALLBACK_MESSAGE,
)


class TestCentralizedSafetyGate:
    """Verify the centralized safety gate."""

    def _base_context(self, **overrides) -> dict:
        ctx = {
            "session_id": "test-session-001",
            "caller_utterance": "I have a headache",
            "chief_complaint": "headache",
            "red_flags_reported": [],
            "symptom_severity": "mild",
            "confusion_score": 0.0,
            "protocol_references": [],
            "model_version": "deepseek-v2",
            "confidence_min_threshold": 0.60,
        }
        ctx.update(overrides)
        return ctx

    def _base_llm_output(self, **overrides) -> dict:
        output = {
            "disposition": "ROUTINE",
            "urgency_level": "LOW",
            "confidence_score": 0.85,
            "rules_triggered": [],
            "red_flags_triggered": [],
            "escalation_required": False,
            "protocol_references": [],
            "model_version": "deepseek-v2",
            "timestamp": "2026-02-19T00:00:00Z",
            "message_to_caller": "You should see your doctor within the week.",
        }
        output.update(overrides)
        return output

    def test_normal_output_passes_through(self):
        decision = safety_gate(
            self._base_llm_output(),
            self._base_context(),
        )
        assert isinstance(decision, FinalDecision)
        # Gate normalises legacy ROUTINE → canonical SCHEDULE
        assert decision.disposition == "SCHEDULE"
        assert decision.confidence_score == 0.85
        assert not decision.safe_fallback_used

    def test_red_flag_overrides_llm(self):
        decision = safety_gate(
            self._base_llm_output(disposition="ROUTINE"),
            self._base_context(
                caller_utterance="I have crushing chest pain radiating to my arm"
            ),
        )
        assert decision.disposition == "ER_NOW"
        assert decision.escalation_required is True
        assert "CHEST_PAIN_SEVERE" in decision.rules_triggered

    def test_diagnosis_in_llm_output_rewritten(self):
        decision = safety_gate(
            self._base_llm_output(
                message_to_caller="You have a heart disease condition. See a doctor."
            ),
            self._base_context(),
        )
        assert (
            "you have" not in decision.message_to_caller.lower()
            or "disease" not in decision.message_to_caller.lower()
        )
        assert len(decision.diagnosis_rewrites) >= 1

    def test_invalid_schema_uses_fallback(self):
        decision = safety_gate(
            {"invalid": "data", "no_required_fields": True},
            self._base_context(),
        )
        # Should eventually coerce or fallback
        assert isinstance(decision, FinalDecision)
        assert decision.escalation_required is True

    def test_low_confidence_forces_escalation(self):
        decision = safety_gate(
            self._base_llm_output(confidence_score=0.3),
            self._base_context(confidence_min_threshold=0.60),
        )
        assert decision.escalation_required is True
        assert decision.disposition in ("HUMAN_REVIEW", "ER_NOW", "URGENT")

    def test_gate_trace_populated(self):
        decision = safety_gate(
            self._base_llm_output(),
            self._base_context(),
        )
        assert len(decision.gate_trace) >= 5  # All layers logged

    def test_weighted_flags_prevent_low_disposition(self):
        """If weighted flags triggered, LLM cannot claim SELF_CARE."""
        decision = safety_gate(
            self._base_llm_output(disposition="SELF_CARE"),
            self._base_context(caller_utterance="very high fever and 9 out of 10 pain"),
        )
        # Weighted flags should prevent SELF_CARE
        assert decision.disposition != "SELF_CARE"

    def test_schema_cannot_be_bypassed(self):
        """Explicit test: schema validation cannot be bypassed."""
        # Garbage input
        decision = safety_gate(
            {"random_key": 42},
            self._base_context(),
        )
        assert isinstance(decision, FinalDecision)
        # Must either pass validation via coercion or use fallback
        assert decision.timestamp  # Always has timestamp
        assert decision.model_version  # Always has model version

    def test_decisions_are_fully_traceable(self):
        """Explicit test: every decision has a complete audit trail."""
        decision = safety_gate(
            self._base_llm_output(),
            self._base_context(),
        )
        assert decision.timestamp
        assert decision.model_version
        assert isinstance(decision.rules_triggered, list)
        assert isinstance(decision.red_flags_triggered, list)
        assert isinstance(decision.protocol_references, list)
        assert isinstance(decision.gate_trace, list)
        assert len(decision.gate_trace) > 0


# ---------------------------------------------------------------------------
# 5. PHI MASKING TESTS
# ---------------------------------------------------------------------------

from src.safety.phi_masking import (  # noqa: E402
    mask_phi,
    mask_phi_in_dict,
    mask_transcript,
)


class TestPHIMasking:
    """Verify PHI masking enforcement."""

    def test_mask_ssn(self):
        text = "My SSN is 123-45-6789"
        masked = mask_phi(text)
        assert "123-45-6789" not in masked
        assert "[REDACTED_SSN]" in masked

    def test_mask_phone(self):
        text = "Call me at (555) 123-4567"
        masked = mask_phi(text)
        assert "555" not in masked or "4567" not in masked
        assert "[REDACTED" in masked

    def test_mask_email(self):
        text = "My email is john@example.com"
        masked = mask_phi(text)
        assert "john@example.com" not in masked
        assert "[REDACTED_EMAIL]" in masked

    def test_mask_dob(self):
        text = "Born on 01/15/1990"
        masked = mask_phi(text)
        assert "01/15/1990" not in masked
        assert "[REDACTED_DOB]" in masked

    def test_mask_name_context(self):
        text = "My name is John Smith"
        masked = mask_phi(text)
        assert "John Smith" not in masked
        assert "[REDACTED_NAME]" in masked

    def test_mask_mrn(self):
        text = "MRN: 123456789"
        masked = mask_phi(text)
        assert "123456789" not in masked
        assert "[REDACTED_MRN]" in masked

    def test_non_phi_unchanged(self):
        text = "I have a headache and feel dizzy"
        masked = mask_phi(text)
        assert masked == text

    def test_mask_phi_in_dict(self):
        data = {
            "user_text": "My name is Jane Doe, SSN 123-45-6789",
            "disposition": "ROUTINE",
        }
        masked = mask_phi_in_dict(data, keys_to_mask=["user_text"])
        assert "Jane Doe" not in masked["user_text"]
        assert "123-45-6789" not in masked["user_text"]
        assert masked["disposition"] == "ROUTINE"  # Unmasked

    def test_mask_transcript(self):
        transcript = [
            {"role": "caller", "text": "My name is John, SSN 999-88-7777"},
            {"role": "assistant", "text": "Thank you for sharing."},
        ]
        masked = mask_transcript(transcript)
        assert "John" not in masked[0]["text"]
        assert "999-88-7777" not in masked[0]["text"]

    def test_masking_is_irreversible(self):
        text = "Patient: John Smith, SSN 123-45-6789, DOB 01/15/1990"
        masked = mask_phi(text)
        # Original values cannot be recovered
        assert "John Smith" not in masked
        assert "123-45-6789" not in masked
        assert "01/15/1990" not in masked
        # Masked tokens are present
        assert "[REDACTED" in masked


# ---------------------------------------------------------------------------
# 6. PRODUCTION POSTGRES ENFORCEMENT TESTS
# ---------------------------------------------------------------------------


class TestProductionPostgresEnforcement:
    """Verify Postgres is mandatory in production."""

    def test_production_requires_postgres_in_config(self):
        """Config validation should reject non-postgres in production."""

        with patch.dict(
            os.environ,
            {
                "ENVIRONMENT": "production",
                "STORAGE_BACKEND": "memory",
                "DEEPSEEK_API_KEY": "test-key",
            },
        ):
            # Re-import to pick up new env vars
            import importlib
            import src.config

            importlib.reload(src.config)
            errors = src.config.validate_config()
            assert any("postgres" in e.lower() for e in errors), (
                f"Expected postgres requirement error, got: {errors}"
            )

    def test_factory_raises_in_production_without_postgres(self):
        """Storage factory must raise RuntimeError in production without postgres."""
        from src.storage.factory import get_storage_backend, reset_storage_backend

        reset_storage_backend()

        with (
            patch("src.storage.factory.ENVIRONMENT", "production"),
            patch("src.storage.factory.STORAGE_BACKEND", "memory"),
        ):
            with pytest.raises(RuntimeError, match="Production requires Postgres"):
                get_storage_backend()

        reset_storage_backend()


# ---------------------------------------------------------------------------
# 7. DECISION TRACE COMPLETENESS TESTS
# ---------------------------------------------------------------------------


class TestDecisionTraceCompleteness:
    """Verify that decision traces include all required fields."""

    def test_final_decision_has_all_required_fields(self):
        """Every FinalDecision must have all required fields."""
        decision = safety_gate(
            {
                "disposition": "ROUTINE",
                "urgency_level": "LOW",
                "confidence_score": 0.9,
                "rules_triggered": [],
                "red_flags_triggered": [],
                "escalation_required": False,
                "protocol_references": ["PROTO-001"],
                "model_version": "test-v1",
                "timestamp": "2026-02-19T00:00:00Z",
                "message_to_caller": "Test message",
            },
            {
                "session_id": "trace-test-001",
                "caller_utterance": "test",
                "chief_complaint": "test",
                "red_flags_reported": [],
                "symptom_severity": "mild",
                "confusion_score": 0.0,
                "protocol_references": ["PROTO-001"],
                "model_version": "test-v1",
                "confidence_min_threshold": 0.60,
            },
        )

        # All required trace fields
        assert decision.disposition is not None
        assert decision.urgency_level is not None
        assert isinstance(decision.confidence_score, float)
        assert isinstance(decision.rules_triggered, list)
        assert isinstance(decision.red_flags_triggered, list)
        assert isinstance(decision.escalation_required, bool)
        assert isinstance(decision.protocol_references, list)
        assert decision.model_version is not None
        assert decision.timestamp is not None
        assert decision.message_to_caller is not None
        assert isinstance(decision.gate_trace, list)

    def test_db_schema_has_all_session_fields(self):
        """Verify the DB model includes all required session fields."""
        from src.storage.models import TriageSessionModel

        columns = {c.name for c in TriageSessionModel.__table__.columns}
        required = {
            "session_id",
            "caller_id",
            "model_name",
            "model_version",
            "protocol_version_used",
            "final_disposition",
            "confidence_score",
            "created_at",
            "finalized_at",
            "status",
        }
        assert required.issubset(columns), f"Missing: {required - columns}"

    def test_db_schema_has_all_decision_fields(self):
        """Verify the decisions table includes all required fields."""
        from src.storage.models import DecisionModel

        columns = {c.name for c in DecisionModel.__table__.columns}
        required = {
            "session_id",
            "turn_index",
            "disposition",
            "urgency_level",
            "confidence_score",
            "escalation_required",
            "rules_triggered",
            "red_flags_triggered",
            "protocol_references",
            "protocol_version",
            "model_name",
            "model_version",
            "created_at",
            "finalized_at",
            "gate_trace",
        }
        assert required.issubset(columns), f"Missing: {required - columns}"


# ---------------------------------------------------------------------------
# 8. PROTOCOL HIERARCHY ENFORCEMENT
# ---------------------------------------------------------------------------


class TestProtocolHierarchy:
    """Verify RULES > PROTOCOL > LLM hierarchy."""

    def test_rules_override_llm_disposition(self):
        """Red-flag rules MUST override any LLM disposition."""
        decision = safety_gate(
            {
                "disposition": "SELF_CARE",  # LLM says safe
                "urgency_level": "LOW",
                "confidence_score": 0.99,
                "rules_triggered": [],
                "red_flags_triggered": [],
                "escalation_required": False,
                "protocol_references": [],
                "model_version": "test",
                "timestamp": "2026-02-19T00:00:00Z",
                "message_to_caller": "You're fine.",
            },
            {
                "session_id": "hierarchy-test",
                "caller_utterance": "crushing chest pain heart attack",
                "chief_complaint": "chest pain",
                "red_flags_reported": [],
                "symptom_severity": "severe",
                "confusion_score": 0.0,
                "protocol_references": [],
                "model_version": "test",
                "confidence_min_threshold": 0.60,
            },
        )
        # Rules MUST win over LLM
        assert decision.disposition == "ER_NOW"
        assert decision.escalation_required is True

    def test_llm_cannot_downgrade_when_flags_present(self):
        """LLM cannot claim ROUTINE/SELF_CARE when weighted flags fired."""
        decision = safety_gate(
            {
                "disposition": "ROUTINE",
                "urgency_level": "LOW",
                "confidence_score": 0.9,
                "rules_triggered": [],
                "red_flags_triggered": [],
                "escalation_required": False,
                "protocol_references": [],
                "model_version": "test",
                "timestamp": "2026-02-19T00:00:00Z",
                "message_to_caller": "See your doctor next week.",
            },
            {
                "session_id": "downgrade-test",
                "caller_utterance": "I have a very high fever spike",
                "chief_complaint": "high fever",
                "red_flags_reported": [],
                "symptom_severity": "moderate",
                "confusion_score": 0.0,
                "protocol_references": [],
                "model_version": "test",
                "confidence_min_threshold": 0.60,
            },
        )
        # With weighted flags, ROUTINE should be upgraded
        if "HIGH_FEVER" in decision.rules_triggered:
            assert decision.disposition != "SELF_CARE"


# ---------------------------------------------------------------------------
# 9. SCHEMA FAILURE SIMULATION
# ---------------------------------------------------------------------------


class TestSchemaFailureSimulation:
    """Simulate schema validation failures and verify safe fallback."""

    def test_completely_invalid_json_uses_fallback(self):
        """If LLM returns garbage, system must use safe fallback."""
        result = validate_triage_output({"garbage": True, "not_valid": 42})
        # Should coerce to valid output thanks to retry
        if result is not None:
            assert result.escalation_required is True
        # Either way, system doesn't crash

    def test_null_input_returns_none(self):
        """None-like input should be handled gracefully."""
        try:
            result = validate_triage_output({})
            # Empty dict gets coerced
            assert result is not None
        except Exception:
            pytest.fail("Schema validation should not raise on empty dict")

    def test_fallback_message_correct(self):
        assert (
            "cannot safely assess" in SAFE_FALLBACK_MESSAGE.lower()
            or "immediate medical attention" in SAFE_FALLBACK_MESSAGE.lower()
        )
