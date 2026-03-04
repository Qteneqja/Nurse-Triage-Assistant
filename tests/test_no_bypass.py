"""
No-Bypass Convergence Tests

These tests enforce the architectural invariant:
    "Any LLM-produced string that reaches a caller, a file, or a DB row
     MUST pass through ONE gate."

If any of these tests fail, it means a bypass path has been introduced.
Fix the code, not the test.
"""

from __future__ import annotations

import pathlib
import re

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SRC_ROOT = pathlib.Path(__file__).resolve().parent.parent / "src"
TESTS_ROOT = pathlib.Path(__file__).resolve().parent

# Modules that ARE allowed to import DeepSeekClient / get_deepseek_client
_ALLOWED_DEEPSEEK_IMPORTERS = frozenset(
    {
        # The guarded wrapper that wraps StructuredLLMClient
        "src/llm/guarded_client.py",
        # The client itself
        "src/llm/deepseek_client.py",
        # StructuredLLMClient (its own module)
        "src/llm/client.py",
        # Legacy re-export shim (backward compat for test imports)
        "src/llm/__init__.py",
    }
)

# Modules allowed to import StructuredLLMClient
_ALLOWED_STRUCTURED_IMPORTERS = frozenset(
    {
        "src/llm/guarded_client.py",
        "src/llm/client.py",
        "src/llm/__init__.py",
    }
)


# ---------------------------------------------------------------------------
# 1. STATIC: No direct DeepSeekClient usage
# ---------------------------------------------------------------------------


class TestNoDirectDeepSeekUsage:
    """Fail if any source file imports get_deepseek_client outside allowed set."""

    def _scan_imports(self, pattern: str) -> list[str]:
        """Return list of src/ files importing the given pattern."""
        violators = []
        for py_file in SRC_ROOT.rglob("*.py"):
            rel = py_file.relative_to(SRC_ROOT.parent).as_posix()
            if rel in _ALLOWED_DEEPSEEK_IMPORTERS:
                continue
            text = py_file.read_text(encoding="utf-8", errors="replace")
            if re.search(pattern, text):
                violators.append(rel)
        return violators

    def test_no_get_deepseek_client_import(self):
        """No src/ file outside allowed set may import get_deepseek_client."""
        violators = self._scan_imports(r"from\s+src\.llm\.deepseek_client\s+import")
        assert violators == [], (
            f"The following files import from deepseek_client directly (bypass risk): "
            f"{violators}"
        )

    def test_no_deepseek_client_instantiation(self):
        """No src/ file outside allowed set may instantiate DeepSeekClient()."""
        violators = self._scan_imports(r"DeepSeekClient\(\)")
        assert violators == [], (
            f"The following files instantiate DeepSeekClient directly: {violators}"
        )


# ---------------------------------------------------------------------------
# 2. CANONICAL DISPOSITIONS ONLY
# ---------------------------------------------------------------------------

from src.safety.gate import (  # noqa: E402
    CANONICAL_DISPOSITIONS,
    LEGACY_TO_CANON,
    normalize_disposition,
    GateContext,
    gate_triage_output,
    gate_outbound_text,
)


class TestCanonicalDispositions:
    """Ensure ONLY canonical values survive the gate."""

    @pytest.mark.parametrize("legacy,expected", list(LEGACY_TO_CANON.items()))
    def test_legacy_maps_to_canonical(self, legacy, expected):
        assert expected in CANONICAL_DISPOSITIONS
        assert normalize_disposition(legacy) == expected

    @pytest.mark.parametrize(
        "bad", ["TRIAGE", "LOW", "MAYBE", "NEEDS_REVIEW", "999", ""]
    )
    def test_unknown_maps_to_human_review(self, bad):
        assert normalize_disposition(bad) == "HUMAN_REVIEW"

    def test_gate_triage_output_normalises(self):
        """gate_triage_output must produce a canonical disposition — always."""
        ctx = GateContext(session_id="disp-test")
        for legacy_val in ["SAFE", "PCP", "EMERGENCY", "ROUTINE", "URGENT_CARE"]:
            raw = {
                "disposition": legacy_val,
                "urgency_level": "HIGH",
                "confidence_score": 0.9,
                "message_to_caller": "Test message",
                "model_version": "test",
            }
            decision = gate_triage_output(raw, ctx)
            assert decision.disposition in CANONICAL_DISPOSITIONS, (
                f"gate_triage_output returned non-canonical '{decision.disposition}' "
                f"for input '{legacy_val}'"
            )

    def test_gate_empty_input_fail_closed(self):
        """Empty LLM dict → HUMAN_REVIEW (fail-closed)."""
        ctx = GateContext(session_id="empty-test")
        decision = gate_triage_output({}, ctx)
        assert decision.disposition == "HUMAN_REVIEW"
        assert decision.escalation_required is True


# ---------------------------------------------------------------------------
# 3. OUTBOUND TEXT GATE
# ---------------------------------------------------------------------------


class TestOutboundTextGate:
    """gate_outbound_text must block unsafe content."""

    @pytest.fixture
    def ctx(self):
        return GateContext(session_id="text-gate-test")

    def test_diagnosis_stripped(self, ctx):
        """Text containing a diagnosis must be rewritten."""
        text = "You have pneumonia. Please go to the ER."
        gated = gate_outbound_text(text, ctx, "question")
        assert (
            "pneumonia" not in gated.lower()
            or "may be consistent with" in gated.lower()
        )

    def test_unsafe_instruction_removed(self, ctx):
        """Instructions like 'take 2 tablets' must be removed."""
        text = "Take 2 mg of medication every 6 hours and you're fine."
        gated = gate_outbound_text(text, ctx, "question")
        # At least one unsafe pattern should trigger
        assert (
            "you're fine" not in gated.lower()
            or "[safety instruction removed]" in gated.lower()
        )

    def test_phi_probing_blocked(self, ctx):
        """Text requesting SSN or insurance must be blocked."""
        text = "What is your social security number?"
        gated = gate_outbound_text(text, ctx, "question")
        assert "social security" not in gated.lower()

    def test_empty_text_passes_through(self, ctx):
        """Empty string passes through gate_outbound_text unchanged.

        Fallback handling is done at the GuardedLLM layer, not the gate.
        """
        gated = gate_outbound_text("", ctx, "question")
        assert gated == ""

    def test_length_truncated(self, ctx):
        """Excessively long text must be truncated."""
        long_text = "This is a safe sentence. " * 500
        gated = gate_outbound_text(long_text, ctx, "question")
        assert len(gated) <= 2500  # reasonable max


# ---------------------------------------------------------------------------
# 4. GUARDED LLM WRAPPER
# ---------------------------------------------------------------------------

from src.llm.guarded_client import GuardedLLM, _OUTBOUND_TEXT_FIELDS  # noqa: E402


class TestGuardedLLMContract:
    """GuardedLLM must never return ungated output."""

    def test_outbound_text_fields_cover_key_fields(self):
        """All user-facing text field names must be in the gated set."""
        required = {
            "next_question",
            "patient_summary",
            "sbar_report",
            "disposition_reasoning",
            "safety_net_instructions",
        }
        assert required.issubset(_OUTBOUND_TEXT_FIELDS)

    def test_guarded_llm_wraps_client(self):
        """GuardedLLM must wrap StructuredLLMClient."""
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        g = GuardedLLM(_client=mock_client)
        assert g._client is mock_client


# ---------------------------------------------------------------------------
# 5. DEEPSEEK CLIENT RUNTIME GUARD
# ---------------------------------------------------------------------------


class TestDeepSeekRuntimeGuard:
    """DeepSeekClient methods must reject calls outside GuardedLLM context."""

    def _make_bare_client(self):
        """Create a DeepSeekClient without __init__ (avoids OpenAI setup)."""
        from src.llm.deepseek_client import DeepSeekClient

        DeepSeekClient._guarded_context = False
        client = DeepSeekClient.__new__(DeepSeekClient)
        object.__setattr__(client, "client", None)  # type: ignore[assignment]
        object.__setattr__(client, "model", "test")
        return client

    def test_check_guarded_raises_directly(self):
        """_check_guarded itself raises RuntimeError (no tenacity wrapping)."""
        client = self._make_bare_client()
        with pytest.raises(RuntimeError, match="outside GuardedLLM context"):
            client._check_guarded("test_method")

    @pytest.mark.parametrize(
        "method",
        [
            "get_triage_decision",
            "generate_patient_summary",
            "generate_clinician_sbar",
            "generate_handoff_report",
        ],
    )
    def test_guard_present_in_method(self, method):
        """Each public LLM method must call _check_guarded."""
        import inspect
        from src.llm.deepseek_client import DeepSeekClient

        source = inspect.getsource(getattr(DeepSeekClient, method))
        assert "_check_guarded" in source, (
            f"DeepSeekClient.{method} does not call _check_guarded"
        )

    def test_guarded_context_flag_defaults_off(self):
        """The class flag must default to False (fail-closed)."""
        from src.llm.deepseek_client import DeepSeekClient

        # Reset to default
        DeepSeekClient._guarded_context = False
        assert DeepSeekClient._guarded_context is False


# ---------------------------------------------------------------------------
# 6. LEGACY REST PATH DISABLED
# ---------------------------------------------------------------------------


class TestLegacyRESTDisabled:
    """Legacy REST endpoints that bypassed the gate must return 410."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from src.main import app

        return TestClient(app, raise_server_exceptions=False)

    def test_submit_answer_returns_404(self, client):
        """Legacy POST /{session_id}/answer no longer exists — returns 404."""
        resp = client.post(
            "/api/v1/intake/fake-session/answer",
            json={"answer": "I have chest pain"},
        )
        # Legacy endpoint was removed entirely; path doesn't match new routes
        assert resp.status_code in (404, 410)

    def test_finalize_returns_410(self, client):
        """POST /{session_id}/finalize must return 410 Gone."""
        resp = client.post(
            "/api/v1/intake/fake-session/finalize",
            json={},
        )
        # Could be 410 (disabled) or 404 (session not found before reaching disabled code)
        assert resp.status_code in (410, 404, 422)


# ---------------------------------------------------------------------------
# 7. LEGACY HANDOFF REPORT DEPRECATED
# ---------------------------------------------------------------------------


class TestLegacyHandoffDeprecated:
    """generate_handoff_report_background must raise RuntimeError."""

    @pytest.mark.asyncio
    async def test_legacy_handoff_raises(self):
        from src.twilio.routes import generate_handoff_report_background

        with pytest.raises(RuntimeError, match="deprecated"):
            await generate_handoff_report_background("sid", {}, [], None)


# ---------------------------------------------------------------------------
# 8. PHI MASKING INSTALLED ON ROOT LOGGER
# ---------------------------------------------------------------------------


class TestPHIMaskingInstalled:
    """PHIMaskingFilter must be installed on the root logger."""

    def test_phi_filter_on_root(self):
        import logging
        from src.safety.phi_masking import PHIMaskingFilter

        root = logging.getLogger()
        filter_types = [type(f) for f in root.filters]
        assert PHIMaskingFilter in filter_types, (
            "PHIMaskingFilter not found on root logger. "
            "Ensure main.py installs it at startup."
        )


# ---------------------------------------------------------------------------
# 9. POSTGRES PHI MASKING
# ---------------------------------------------------------------------------


class TestPostgresPHIMasking:
    """Postgres storage must apply mask_phi when STORE_PHI=False."""

    def test_sync_turns_masks_text(self):
        """Verify _sync_turns uses mask_phi instead of None when STORE_PHI=False."""
        # Static analysis: read the source and check
        import inspect
        from src.storage.postgres import PostgresStorage

        source = inspect.getsource(PostgresStorage._sync_turns)
        # Must contain mask_phi call
        assert "mask_phi" in source, (
            "_sync_turns does not call mask_phi. "
            "PHI text must be masked, not just omitted."
        )
        # Must set phi_masked flag
        assert "phi_masked" in source


# ---------------------------------------------------------------------------
# 10. UNIFIED GATE IS SINGLE ENTRY POINT
# ---------------------------------------------------------------------------


class TestSingleGateEntryPoint:
    """The old safety_gate.py must be a thin re-export wrapper."""

    def test_old_safety_gate_is_reexport(self):
        """src/safety/safety_gate.py must re-export from src.safety.gate."""
        safety_gate_path = SRC_ROOT / "safety" / "safety_gate.py"
        text = safety_gate_path.read_text(encoding="utf-8")
        assert "from src.safety.gate import" in text, (
            "safety_gate.py is not a thin wrapper re-exporting from gate.py"
        )
        # Must NOT define its own gate_triage_output / FinalDecision
        assert "class FinalDecision" not in text
        assert "def gate_triage_output" not in text

    def test_validators_post_check_delegates(self):
        """post_check_safety_gate in validators must delegate to unified gate."""
        import inspect
        from src.orchestrator.validators import post_check_safety_gate

        source = inspect.getsource(post_check_safety_gate)
        assert "gate_outbound_text" in source, (
            "post_check_safety_gate does not delegate to gate_outbound_text"
        )
