"""
Pytest configuration and shared fixtures for Phase 4 tests.
"""

import json
import uuid
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from typing import Dict, List, Optional

from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.schemas import (
    AuditTrace,
    OrchestratorSession,
    IntakeTurnOutput,
    FinalizeOutput,
    DispositionCategory,
)
from src.storage.interface import StorageInterface


# ============================================================================
# Golden Call Fixtures
# ============================================================================


@pytest.fixture(scope="session")
def golden_calls_dir() -> Path:
    """Return the path to the golden calls directory."""
    return Path(__file__).parent / "golden_calls"


@pytest.fixture(scope="session")
def golden_calls(golden_calls_dir: Path) -> Dict[str, dict]:
    """Load all golden call JSON files and return as dict keyed by case_id."""
    cases = {}
    if not golden_calls_dir.exists():
        return cases

    for json_file in sorted(golden_calls_dir.glob("*.json")):
        try:
            with open(json_file) as f:
                case = json.load(f)
                case_id = case.get("case_id")
                if case_id:
                    cases[case_id] = case
        except (json.JSONDecodeError, IOError) as e:
            print(f"Failed to load {json_file}: {e}")

    return cases


@pytest.fixture
def golden_case_life_threatening(golden_calls: Dict[str, dict]) -> dict:
    """Return a life-threatening case (e.g., chest pain)."""
    for case in golden_calls.values():
        if case.get("metadata", {}).get("category") == "life_threatening":
            return case
    pytest.skip("No life-threatening case found")


@pytest.fixture
def golden_case_moderate(golden_calls: Dict[str, dict]) -> dict:
    """Return a moderate case for testing SBAR-first behavior."""
    for case in golden_calls.values():
        if case.get("metadata", {}).get("category") == "moderate":
            return case
    pytest.skip("No moderate case found")


# ============================================================================
# Mock Storage
# ============================================================================


class MockStorage(StorageInterface):
    """In-memory mock storage for testing.

    Implements every abstract method from StorageInterface using plain
    dictionaries so that tests are deterministic and fully isolated.
    """

    def __init__(self):
        self.sessions: Dict[str, OrchestratorSession] = {}
        self.transcripts: Dict[str, List[dict]] = {}
        self._call_index: Dict[str, str] = {}  # call_sid → session_id

    # ---- StorageInterface abstract method implementations ----

    def create_session(self, call_sid: str | None = None) -> OrchestratorSession:
        """Create a new session.  When *call_sid* is supplied it is also
        used as the deterministic *session_id* (handy for tests).
        """
        session_id = call_sid or str(uuid.uuid4())
        session = OrchestratorSession(
            session_id=session_id,
            call_sid=call_sid,
            audit_trace=AuditTrace(session_id=session_id, call_sid=call_sid),
        )
        self.sessions[session_id] = session
        self.transcripts[session_id] = []
        if call_sid:
            self._call_index[call_sid] = session_id
        return session

    def save_session(self, session: OrchestratorSession) -> None:
        """Persist (upsert) a session."""
        self.sessions[session.session_id] = session

    def load_session(self, session_id: str) -> Optional[OrchestratorSession]:
        """Load a session by ID."""
        return self.sessions.get(session_id)

    def load_session_by_call(self, call_sid: str) -> Optional[OrchestratorSession]:
        """Load a session by Twilio CallSid."""
        session_id = self._call_index.get(call_sid)
        if session_id is None:
            return None
        return self.sessions.get(session_id)

    def delete_session(self, session_id: str) -> None:
        """Remove a session and its transcripts."""
        session = self.sessions.pop(session_id, None)
        self.transcripts.pop(session_id, None)
        if session and session.call_sid:
            self._call_index.pop(session.call_sid, None)

    # ---- Convenience aliases used by tests ----

    def get_session(self, session_id: str) -> Optional[OrchestratorSession]:
        """Alias for load_session (backward-compat for test code)."""
        return self.load_session(session_id)

    def update_session(self, session: OrchestratorSession) -> None:
        """Alias for save_session (backward-compat for test code)."""
        self.save_session(session)

    def save_transcript(self, session_id: str, turn: dict) -> bool:
        """Append a turn to the transcript log (test-only helper)."""
        if session_id not in self.transcripts:
            self.transcripts[session_id] = []
        self.transcripts[session_id].append(turn)
        return True

    def get_active_session_count(self) -> int:
        return len(self.sessions)

    def check_connectivity(self) -> bool:
        return True


@pytest.fixture
def mock_storage() -> MockStorage:
    """Return a fresh mock storage instance."""
    return MockStorage()


# ============================================================================
# Mock LLM
# ============================================================================


class MockLLMClient:
    """Mock LLM client for deterministic testing."""

    def __init__(self, default_response: Optional[dict] = None):
        self.call_count = 0
        self.call_history: List[dict] = []
        self.default_response = default_response or {
            "confidence_score": 0.75,
            "escalation_required": False,
            "red_flags_triggered": [],
            "rules_triggered": [],
            "next_action": "ASK_QUESTION",
            "disposition": "HUMAN_REVIEW",
            "next_question": "What else can you tell me?",
        }

    async def structured_call(
        self,
        system_prompt: str = "",
        user_message: str = "",
        output_schema: Optional[type] = None,
        retry_count: int = 3,
        **kwargs,
    ):
        """Mock structured LLM call."""
        self.call_count += 1
        self.call_history.append(
            {
                "system_prompt": system_prompt,
                "user_message": user_message,
                "schema": output_schema.__name__
                if output_schema and hasattr(output_schema, "__name__")
                else str(output_schema),
            }
        )

        # Return the most appropriate safe default for the requested schema.
        from src.orchestrator.schemas import (
            IntakeTurnOutput,
            FinalizeOutput,
            Phase1TurnOutput,
        )
        from src.orchestrator.validators import (
            safe_intake_turn_default,
            safe_finalize_default,
            safe_phase1_escalation,
        )

        if output_schema is IntakeTurnOutput:
            return safe_intake_turn_default()
        if output_schema is FinalizeOutput:
            return safe_finalize_default()
        if output_schema is Phase1TurnOutput:
            try:
                return Phase1TurnOutput(**self.default_response)
            except Exception:
                return safe_phase1_escalation()
        # Generic fallback
        try:
            return output_schema(**self.default_response)  # type: ignore[misc]
        except Exception:
            return MagicMock(spec=output_schema)

    async def raw_call(self, system_prompt: str, user_message: str, **kwargs) -> str:
        """Mock raw LLM call."""
        self.call_count += 1
        return json.dumps(self.default_response)

    def set_response(self, response: dict):
        """Set the response for next calls."""
        self.default_response.update(response)


@pytest.fixture
def mock_llm_client() -> MockLLMClient:
    """Return a fresh mock LLM client."""
    return MockLLMClient()


# ============================================================================
# Mock Orchestrator
# ============================================================================


@pytest_asyncio.fixture
async def orchestrator_with_mocks(
    mock_storage: MockStorage,
    mock_llm_client: MockLLMClient,
) -> Orchestrator:
    """Return an orchestrator with mocked GuardedLLM and no-op retriever.

    mock_storage is available to tests directly; the orchestrator receives
    sessions as arguments so it does not need direct storage wiring.
    mock_llm_client.structured_call is called through the bridge, so
    patch.object(mock_llm_client, 'structured_call') works in tests.
    """
    from src.llm.guarded_client import GuardedLLM
    from src.protocols.retriever import ProtocolRetriever

    # Build a GuardedLLM stub whose structured_call delegates to
    # mock_llm_client at call-time so patch.object() works in tests.
    mock_guarded = MagicMock(spec=GuardedLLM)

    async def _bridge(*, messages, output_schema, ctx, **kw):
        # Resolves mock_llm_client.structured_call at call-time,
        # so patch.object(mock_llm_client, 'structured_call') takes effect.
        result = await mock_llm_client.structured_call(
            system_prompt="",
            user_message="",
            output_schema=output_schema,
        )
        # Guard: schema-unaware test mocks may return wrong type.
        # Fall back to safe defaults when there is a mismatch.
        if output_schema is not None and not isinstance(result, output_schema):
            from src.orchestrator.validators import (
                safe_finalize_default,
                safe_intake_turn_default,
            )

            if output_schema is FinalizeOutput:
                return safe_finalize_default()
            if output_schema is IntakeTurnOutput:
                return safe_intake_turn_default()
        return result

    mock_guarded.structured_call = AsyncMock(side_effect=_bridge)

    # No-op retriever — tests don't need real protocol files.
    mock_retriever = MagicMock(spec=ProtocolRetriever)
    mock_retriever.retrieve.return_value = []

    return Orchestrator(guarded_llm=mock_guarded, protocol_retriever=mock_retriever)


# ============================================================================
# Helper Functions
# ============================================================================


def create_test_session(
    session_id: str = "test-001",
    max_turns: int = 12,
    confidence_threshold: float = 0.75,
) -> OrchestratorSession:
    """Create a test orchestrator session."""
    return OrchestratorSession(
        session_id=session_id,
        max_turns=max_turns,
        confidence_threshold=confidence_threshold,
    )


def create_intake_turn_output(
    next_question: str = "What else?",
    confidence: float = 0.75,
    disposition_hint: str = "HUMAN_REVIEW",
    **kwargs,
) -> IntakeTurnOutput:
    """Create a test IntakeTurnOutput."""
    defaults = {
        "extracted_fields_update": {},
        "missing_fields_prioritized": [],
        "next_question": next_question,
        "llm_safety_flags": [],
        "confidence": confidence,
    }
    defaults.update(kwargs)
    return IntakeTurnOutput(**defaults)


def create_finalize_output(
    disposition: str = DispositionCategory.SCHEDULE,
    sbar_text: str = "S: patient complaint\nB: background\nA: assessment\nR: recommendation",
    **kwargs,
) -> FinalizeOutput:
    """Create a test FinalizeOutput."""
    defaults = {
        "disposition": DispositionCategory(disposition),
        "disposition_reasoning": "Test reasoning",
        "safety_net_instructions": "Seek care if symptoms worsen",
        "sbar_report": sbar_text,
        "patient_summary": "Patient summary",
        "llm_safety_flags": [],
    }
    defaults.update(kwargs)
    return FinalizeOutput(**defaults)


@pytest.fixture
def assert_helpers():
    """Return assertion helper functions."""

    def assert_sbar_present(output: FinalizeOutput) -> bool:
        """Assert that SBAR is present and non-empty."""
        assert output.sbar_report or output.sbar, "SBAR missing"
        sbar_text = output.sbar_report or str(output.sbar)
        assert len(sbar_text) > 20, "SBAR too short"
        return True

    def assert_disposition_valid(disposition: str) -> bool:
        """Assert disposition is a valid enum value."""
        valid = {"ER_NOW", "URGENT", "SCHEDULE", "SELF_CARE", "HUMAN_REVIEW"}
        assert disposition in valid, f"Invalid disposition: {disposition}"
        return True

    def assert_red_flags_triggered(
        result: dict,
        expected_flags: List[str],
        must_have_all: bool = True,
    ) -> bool:
        """Assert expected red flags were triggered."""
        triggered = set(result.get("red_flags_triggered", []))
        expected = set(expected_flags)

        if must_have_all:
            assert expected.issubset(triggered), (
                f"Missing flags: {expected - triggered}"
            )
        else:
            assert len(expected & triggered) > 0, (
                f"None of {expected} found in {triggered}"
            )

        return True

    return {
        "assert_sbar_present": assert_sbar_present,
        "assert_disposition_valid": assert_disposition_valid,
        "assert_red_flags_triggered": assert_red_flags_triggered,
    }


# ============================================================================
# Environment Setup
# ============================================================================


@pytest.fixture(scope="session", autouse=True)
def setup_test_env():
    """Set up test environment variables."""
    import os

    os.environ["USE_MOCK_LLM"] = "true"
    os.environ["STORAGE_BACKEND"] = "memory"
    os.environ["ENVIRONMENT"] = "test"
    os.environ["PROTOCOL_VERSION"] = "v1"


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Make tests hermetic w.r.t. the global rate limiter.

    The middleware keeps a module-level sliding-window store keyed by client IP;
    every TestClient request shares the IP "testclient", so without a reset the
    whole suite accumulates into one 60s window and a later test can be 429'd
    purely because earlier tests made requests. Clear it before each test.
    """
    try:
        from src.security.middleware import _rate_limiter

        _rate_limiter._requests.clear()
    except Exception:
        pass
    yield


# ============================================================================
# Parametrize Helpers
# ============================================================================


def pytest_generate_tests(metafunc):
    """Generate parametrized tests from fixtures."""
    # Automatically parametrize tests using golden_calls fixture
    if "golden_case" in metafunc.fixturenames:
        pass  # Let pytest handle it naturally
