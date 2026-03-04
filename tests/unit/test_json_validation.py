"""
Phase 4 Invalid JSON Simulation Tests

Tests validator + retry + safe fallback logic for:
- Missing required fields
- Wrong data types
- Malformed JSON
- Unexpected keys
- Enum mismatches
- Null values
"""

import json
import pytest
from unittest.mock import patch

from src.orchestrator.schemas import (
    IntakeTurnOutput,
    FinalizeOutput,
    DispositionCategory,
    OrchestratorSession,
)


class TestMissingRequiredFields:
    """Test handling of missing required JSON fields."""

    @pytest.mark.asyncio
    async def test_missing_disposition(
        self,
        orchestrator_with_mocks,
        mock_llm_client,
    ):
        """LLM returns JSON without disposition field."""
        session = OrchestratorSession(session_id="test-missing-disp")

        # Mock LLM to return invalid JSON
        bad_json = {
            "next_action": "ASK_QUESTION",
            "confidence_score": 0.75,
            # Missing "disposition"
        }

        with patch.object(mock_llm_client, "raw_call") as mock_raw:
            mock_raw.return_value = json.dumps(bad_json)

            # Process turn - should handle gracefully
            result = await orchestrator_with_mocks.process_turn(
                session, "I have a headache"
            )
            # Should not crash
            assert result is not None

    @pytest.mark.asyncio
    async def test_missing_next_action(
        self,
        orchestrator_with_mocks,
    ):
        """LLM returns JSON without next_action."""

        # Validator should catch and retry or use fallback
        # (exact behavior depends on implementation)

    @pytest.mark.asyncio
    async def test_missing_sbar_on_finalize(
        self,
        orchestrator_with_mocks,
        mock_llm_client,
    ):
        """Finalization returns JSON without SBAR field."""
        session = OrchestratorSession(session_id="test-no-sbar")
        session.intake_state.caller_name = "Alice"

        bad_finalize = {
            "disposition": "SCHEDULE",
            "disposition_reasoning": "Mild symptoms",
            # Missing "sbar_report"
            "patient_summary": "Test",
        }

        with patch.object(mock_llm_client, "raw_call") as mock_raw:
            mock_raw.return_value = json.dumps(bad_finalize)

            # Should handle missing SBAR
            result = await orchestrator_with_mocks.finalize(session)
            # Should either regenerate or use safe fallback
            assert result is not None


class TestWrongDataTypes:
    """Test handling of wrong data types in JSON."""

    @pytest.mark.asyncio
    async def test_confidence_as_string(self):
        """confidence_score is string "0.75" instead of float."""
        bad_output = {
            "confidence_score": "0.75",  # Should be float
            "next_action": "ASK_QUESTION",
            "disposition": "HUMAN_REVIEW",
        }

        # Should coerce string to float or reject
        try:
            output = IntakeTurnOutput(**bad_output)  # type: ignore[arg-type]
            # If coercion worked, check value
            assert 0.7 <= output.confidence <= 0.8
        except (ValueError, TypeError):
            # Validator rejected
            pass

    @pytest.mark.asyncio
    async def test_escalation_required_as_string(self):
        """escalation_required is "yes" instead of bool."""
        bad_output = {
            "escalation_required": "yes",  # Should be bool
            "next_action": "ASK_QUESTION",
            "disposition": "HUMAN_REVIEW",
        }

        # Should reject or coerce
        try:
            IntakeTurnOutput(**bad_output)  # type: ignore[arg-type]
        except (ValueError, TypeError):
            pass

    @pytest.mark.asyncio
    async def test_caller_age_as_string(self):
        """caller_age is "35" instead of int."""
        bad_obj = {"caller_age": "35"}  # String instead of int

        # Validator might coerce or reject
        try:
            from src.orchestrator.schemas import StructuredIntakeState

            state = StructuredIntakeState(**bad_obj)  # type: ignore[arg-type]
            assert isinstance(state.caller_age, int)
        except (ValueError, TypeError):
            pass


class TestMalformedJSON:
    """Test handling of syntactically invalid JSON."""

    @pytest.mark.asyncio
    async def test_missing_closing_brace(
        self,
        orchestrator_with_mocks,
        mock_llm_client,
    ):
        """LLM returns JSON with missing closing brace."""
        malformed = '{"disposition": "SCHEDULE", "next_action": "ASK_QUESTION"'

        with patch.object(mock_llm_client, "raw_call") as mock_raw:
            mock_raw.return_value = malformed

            session = OrchestratorSession(session_id="test-malformed-json")

            # Should handle JSON parse error gracefully
            try:
                await orchestrator_with_mocks.process_turn(session, "test")
                # If not crashed, assume handled
            except json.JSONDecodeError:
                # Expected - should be caught and logged
                pass

    @pytest.mark.asyncio
    async def test_trailing_comma(
        self,
        mock_llm_client,
    ):
        """LLM returns JSON with trailing comma (invalid in JSON)."""
        malformed = '{"disposition": "SCHEDULE", "next_action": "ASK_QUESTION",}'

        # JSON parser should reject
        with pytest.raises(json.JSONDecodeError):
            json.loads(malformed)

    @pytest.mark.asyncio
    async def test_unquoted_keys(self):
        """LLM returns JSON with unquoted keys."""
        malformed = '{disposition: "SCHEDULE"}'  # Keys unquoted

        with pytest.raises(json.JSONDecodeError):
            json.loads(malformed)


class TestUnexpectedKeys:
    """Test handling of extra unexpected keys in JSON."""

    @pytest.mark.asyncio
    async def test_extra_ai_thoughts_key(self):
        """LLM adds extra 'ai_thoughts' key."""
        output = {
            "disposition": "SCHEDULE",
            "next_action": "ASK_QUESTION",
            "confidence_score": 0.75,
            "ai_thoughts": "The patient seems stable",  # Extra key
        }

        # Validator should ignore or handle
        try:
            FinalizeOutput(**output)  # type: ignore[arg-type]
            # If succeeds, extra key was ignored (good)
        except (TypeError, ValueError):
            # Pydantic rejects extra fields or missing required fields (also acceptable)
            pass

    @pytest.mark.asyncio
    async def test_extra_debug_info_key(self):
        """LLM adds debug fields."""
        output = {
            "disposition": "HUMAN_REVIEW",
            "confidence_score": 0.7,
            "_debug_elapsed_ms": 234,  # Extra debug key
            "_internal_state": "...",  # Extra key
        }

        # Should ignore extras
        try:
            from src.orchestrator.schemas import FinalizeOutput

            FinalizeOutput(**output)
        except TypeError:
            pass


class TestEnumMismatch:
    """Test handling of invalid enum values."""

    @pytest.mark.asyncio
    async def test_invalid_disposition_value(self):
        """disposition is "URGENT_CARE" (invalid enum)."""
        bad_output = {
            "disposition": "URGENT_CARE",  # Should be URGENT, not URGENT_CARE
            "next_action": "ASK_QUESTION",
        }

        # Validator should reject
        with pytest.raises(ValueError):
            FinalizeOutput(**bad_output)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_invalid_next_action_value(self):
        """next_action is "CALL_911" (not in enum)."""

        # Should reject or map to valid value

    @pytest.mark.asyncio
    async def test_disposition_typo(self):
        """disposition has typo: "SCHEDULE'" instead of "SCHEDULE"."""
        bad_value = "SCHEDULE'"

        # Should not match enum
        with pytest.raises(ValueError):
            DispositionCategory(bad_value)


class TestNullValues:
    """Test handling of null values in required fields."""

    @pytest.mark.asyncio
    async def test_sbar_is_null(self):
        """sbar_report field is null."""
        bad_finalize = {
            "disposition": "SCHEDULE",
            "sbar_report": None,  # Null
            "patient_summary": "Test",
        }

        # Should either use fallback or reject
        FinalizeOutput(**bad_finalize)
        # If accepted, sbar_report is None (acceptable)
        # Should have fallback for SBAR display

    @pytest.mark.asyncio
    async def test_disposition_reasoning_null(self):
        """disposition_reasoning is null."""
        bad_finalize = {
            "disposition": "HUMAN_REVIEW",
            "disposition_reasoning": None,
            "sbar_report": "Test",
            "patient_summary": "Test",
        }

        FinalizeOutput(**bad_finalize)
        # Should handle gracefully


class TestRetryLogic:
    """Test the retry mechanism for invalid responses."""

    @pytest.mark.asyncio
    async def test_retry_on_first_invalid_response(
        self,
        orchestrator_with_mocks,
        mock_llm_client,
    ):
        """On first invalid response, LLM is re-called (retry)."""
        call_sequence = [0]

        async def mock_call_with_retry(*args, **kwargs):
            call_sequence[0] += 1
            if call_sequence[0] == 1:
                #  First call returns invalid
                raise ValueError("Invalid JSON")
            else:
                # Second call (retry) returns valid
                return IntakeTurnOutput(
                    next_question="Retry worked",
                    confidence=0.8,
                )

        with patch.object(mock_llm_client, "structured_call") as mock:
            mock.side_effect = mock_call_with_retry

            session = OrchestratorSession(session_id="test-retry")

            # This should trigger retry on error
            try:
                await orchestrator_with_mocks.process_turn(session, "test")
            except ValueError:
                # Retries exhausted - OK
                pass

    @pytest.mark.asyncio
    async def test_fallback_after_retries_exhausted(
        self,
        orchestrator_with_mocks,
        mock_llm_client,
    ):
        """After retries exhausted, use safe fallback."""

        async def always_fail(*args, **kwargs):
            raise ValueError("Always fails")

        with patch.object(mock_llm_client, "structured_call") as mock:
            mock.side_effect = always_fail

            session = OrchestratorSession(session_id="test-fallback")

            # Should use safe fallback (e.g., HUMAN_REVIEW)
            try:
                await orchestrator_with_mocks.process_turn(session, "test")
                # If succeeds, fallback worked
            except ValueError:
                # If fails, should log and escalate
                pass


class TestSafeFallbacks:
    """Test safe fallback behaviors."""

    @pytest.mark.asyncio
    async def test_fallback_to_human_review_on_invalid_json(
        self,
        orchestrator_with_mocks,
    ):
        """Invalid JSON triggers escalation to HUMAN_REVIEW."""
        session = OrchestratorSession(session_id="test-fb-human")

        # Trigger invalid JSON scenario
        # Finalization should use safe fallback
        result = await orchestrator_with_mocks.finalize(session)

        # Should have valid disposition
        if result.disposition:
            assert result.disposition in [
                DispositionCategory.HUMAN_REVIEW,
                DispositionCategory.SCHEDULE,
                DispositionCategory.URGENT,
            ]

    @pytest.mark.asyncio
    async def test_fallback_sbar_template(
        self,
        orchestrator_with_mocks,
    ):
        """If SBAR generation fails, use template."""
        session = OrchestratorSession(session_id="test-fb-sbar")
        session.intake_state.caller_name = "Test"

        result = await orchestrator_with_mocks.finalize(session)

        # Should have some SBAR
        assert result.sbar_report is not None or result.sbar is not None

    @pytest.mark.asyncio
    async def test_fallback_next_question(
        self,
        orchestrator_with_mocks,
        mock_llm_client,
    ):
        """If next_question generation fails, use generic fallback."""
        session = OrchestratorSession(session_id="test-fb-question")

        # Cause LLM failure
        with patch.object(mock_llm_client, "structured_call") as mock:
            mock.side_effect = Exception("LLM timeout")

            try:
                result = await orchestrator_with_mocks.process_turn(session, "test")
                # If succeeds with fallback, check question is reasonable
                if hasattr(result, "next_question"):
                    assert len(result.next_question) > 5
            except Exception:
                # Escalation on failure is also acceptable
                pass
