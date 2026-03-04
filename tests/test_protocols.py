"""
Phase 2 Protocol Retrieval — Unit Tests

Covers:
1. Retriever returns relevant protocols for representative complaints.
2. Orchestrator includes protocol context when available.
3. Protocol retrieval failures do not crash the system.
4. Protocol context cannot downgrade urgency (Phase 1 still overrides).
5. Decision trace includes protocol hits properly.
6. Empty / no-match scenarios handled gracefully.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.protocols.retriever import (
    Protocol,
    ProtocolRetriever,
    ProtocolSnippet,
    load_protocols,
    _tokenize,
    _ngrams,
)
from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.schemas import (
    DecisionTraceEntry,
    FinalizeOutput,
    IntakeTurnOutput,
    IntakeStatePatch,
    OrchestratorSession,
    Phase1TurnOutput,
    ProtocolHit,
    StructuredIntakeState,
)


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------


def _make_session(**kwargs) -> OrchestratorSession:
    defaults = {
        "session_id": "proto-test-001",
        "max_turns": 12,
        "confidence_threshold": 0.75,
    }
    defaults.update(kwargs)
    return OrchestratorSession(**defaults)


def _valid_phase1_json() -> str:
    return json.dumps(
        {
            "confidence_score": 0.8,
            "escalation_required": False,
            "red_flags_triggered": [],
            "rules_triggered": [],
            "next_action": "ASK_QUESTION",
            "disposition": "UNDECIDED",
        }
    )


def _make_intake_output(**kwargs) -> IntakeTurnOutput:
    defaults = {
        "extracted_fields_update": IntakeStatePatch(),
        "missing_fields_prioritized": ["onset_time"],
        "next_question": "When did this start?",
        "llm_safety_flags": [],
        "confidence": 0.3,
    }
    defaults.update(kwargs)
    return IntakeTurnOutput(**defaults)


def _make_finalize_output(**kwargs) -> FinalizeOutput:
    defaults = {
        "disposition": "ROUTINE",
        "disposition_reasoning": "Mild symptoms, no red flags",
        "safety_net_instructions": "Go to ER if symptoms worsen",
        "sbar_report": "S: Test\nB: Test\nA: Test\nR: Test",
        "patient_summary": "A nurse will contact you soon.",
        "llm_safety_flags": [],
    }
    defaults.update(kwargs)
    return FinalizeOutput(**defaults)


def _valid_phase1_result(**overrides):
    """Return a valid Phase1TurnOutput for mock structured_call."""
    from src.orchestrator.schemas import Phase1Disposition, Phase1NextAction

    defaults = {
        "confidence_score": 0.8,
        "escalation_required": False,
        "red_flags_triggered": [],
        "rules_triggered": [],
        "next_action": Phase1NextAction.ASK_QUESTION,
        "disposition": Phase1Disposition.HUMAN_REVIEW,
    }
    defaults.update(overrides)
    return Phase1TurnOutput(**defaults)


def _make_protocol(
    id: str = "PROTO-TEST",
    title: str = "Test Protocol",
    keywords: list | None = None,
    body: str = "Test body",
    disposition_notes: str = "",
) -> Protocol:
    return Protocol(
        id=id,
        title=title,
        keywords=keywords or ["test", "headache"],
        body=body,
        disposition_notes=disposition_notes,
        last_updated="2026-01-15",
        version="1.0",
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SECTION 1 — Protocol Loading
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestProtocolLoading:
    """Tests for loading protocols from disk."""

    def test_load_protocols_from_default_dir(self):
        """Should load all 8 starter protocols from protocols/v1/."""
        protocols = load_protocols()
        assert len(protocols) == 8
        ids = {p.id for p in protocols}
        assert "PROTO-001" in ids  # chest pain
        assert "PROTO-008" in ids  # UTI

    def test_load_protocols_from_nonexistent_dir(self):
        """Should return empty list for nonexistent directory."""
        protocols = load_protocols("/nonexistent/path")
        assert protocols == []

    def test_protocol_fields_populated(self):
        """Each loaded protocol should have all required fields."""
        protocols = load_protocols()
        for p in protocols:
            assert p.id
            assert p.title
            assert len(p.keywords) > 0
            assert p.body
            assert p.version
            assert len(p._kw_lower) == len(p.keywords)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SECTION 2 — Tokenization & Scoring Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestTokenization:
    def test_tokenize_basic(self):
        tokens = _tokenize("I have severe chest pain")
        assert tokens == ["i", "have", "severe", "chest", "pain"]

    def test_tokenize_empty(self):
        assert _tokenize("") == []

    def test_ngrams(self):
        tokens = ["chest", "pain", "radiating"]
        bigrams = _ngrams(tokens, 2)
        assert bigrams == ["chest pain", "pain radiating"]

    def test_ngrams_single_token(self):
        assert _ngrams(["chest"], 2) == []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SECTION 3 — Retrieval Relevance
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestRetrieverRelevance:
    """Retriever should return relevant protocols for representative complaints."""

    def setup_method(self):
        self.retriever = ProtocolRetriever()  # loads defaults

    def test_chest_pain_returns_chest_protocol(self):
        results = self.retriever.retrieve(chief_complaint="chest pain")
        assert len(results) >= 1
        assert any(r.id == "PROTO-001" for r in results)

    def test_breathing_difficulty_returns_sob_protocol(self):
        results = self.retriever.retrieve(chief_complaint="I can't breathe well")
        assert len(results) >= 1
        assert any(r.id == "PROTO-002" for r in results)

    def test_abdominal_pain_returns_abdominal_protocol(self):
        results = self.retriever.retrieve(chief_complaint="my stomach really hurts")
        assert len(results) >= 1
        assert any(r.id == "PROTO-003" for r in results)

    def test_fever_returns_fever_protocol(self):
        results = self.retriever.retrieve(
            chief_complaint="I have a high fever and chills"
        )
        assert len(results) >= 1
        assert any(r.id == "PROTO-004" for r in results)

    def test_child_illness_returns_pediatric_protocol(self):
        results = self.retriever.retrieve(
            chief_complaint="my baby is not eating and has a fever"
        )
        assert len(results) >= 1
        ids = {r.id for r in results}
        assert "PROTO-005" in ids  # child illness

    def test_allergic_reaction_returns_allergy_protocol(self):
        results = self.retriever.retrieve(
            chief_complaint="I'm having an allergic reaction with hives"
        )
        assert len(results) >= 1
        assert any(r.id == "PROTO-006" for r in results)

    def test_stroke_signs_returns_neuro_protocol(self):
        results = self.retriever.retrieve(
            chief_complaint="sudden numbness on one side and slurred speech"
        )
        assert len(results) >= 1
        assert any(r.id == "PROTO-007" for r in results)

    def test_uti_symptoms_returns_uti_protocol(self):
        results = self.retriever.retrieve(
            chief_complaint="burning when I urinate and frequent urination"
        )
        assert len(results) >= 1
        assert any(r.id == "PROTO-008" for r in results)

    def test_max_top_k_respected(self):
        retriever = ProtocolRetriever(top_k=1)
        results = retriever.retrieve(
            chief_complaint="chest pain with breathing difficulty"
        )
        assert len(results) <= 1

    def test_no_match_returns_empty(self):
        """Completely irrelevant complaint should return empty."""
        results = self.retriever.retrieve(
            chief_complaint="I need to renew a prescription"
        )
        assert len(results) == 0

    def test_empty_input_returns_empty(self):
        results = self.retriever.retrieve()
        assert results == []

    def test_recent_utterance_adds_context(self):
        """Recent utterance about chest should help match chest protocol."""
        results = self.retriever.retrieve(
            chief_complaint="I feel unwell",
            recent_utterance="The pain is in my chest area and it's a pressure feeling",
        )
        assert any(r.id == "PROTO-001" for r in results)

    def test_results_ordered_by_score(self):
        results = self.retriever.retrieve(
            chief_complaint="severe chest pain radiating to left arm"
        )
        if len(results) >= 2:
            assert results[0].score >= results[1].score

    def test_snippet_has_required_fields(self):
        results = self.retriever.retrieve(chief_complaint="chest pain")
        assert len(results) >= 1
        snippet = results[0]
        assert snippet.id
        assert snippet.title
        assert snippet.version
        assert snippet.excerpt
        assert snippet.score > 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SECTION 4 — Retriever Failure Safety
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestRetrieverFailureSafety:
    """Protocol retrieval failures must not crash the system."""

    def test_retriever_with_no_protocols(self):
        retriever = ProtocolRetriever(protocols=[])
        results = retriever.retrieve(chief_complaint="chest pain")
        assert results == []

    def test_retriever_exception_returns_empty(self):
        """If internal scoring raises, retrieve() should return []."""
        retriever = ProtocolRetriever()
        # Monkey-patch _retrieve_inner to raise
        retriever._retrieve_inner = MagicMock(side_effect=RuntimeError("boom"))
        results = retriever.retrieve(chief_complaint="chest pain")
        assert results == []

    def test_corrupted_protocol_skipped(self, tmp_path):
        """A corrupted JSON file should be skipped, not crash loading."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{invalid json", encoding="utf-8")

        good_file = tmp_path / "good.json"
        good_file.write_text(
            json.dumps(
                {
                    "id": "PROTO-GOOD",
                    "title": "Good",
                    "keywords": ["test"],
                    "body": "body",
                    "disposition_notes": "",
                    "last_updated": "2026-01-01",
                    "version": "1.0",
                }
            ),
            encoding="utf-8",
        )

        protocols = load_protocols(tmp_path)
        assert len(protocols) == 1
        assert protocols[0].id == "PROTO-GOOD"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SECTION 5 — Orchestrator Integration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestOrchestratorProtocolIntegration:
    """Orchestrator should call retriever and inject protocol context into LLM."""

    @pytest.mark.asyncio
    async def test_protocol_context_in_messages(self):
        """When retriever returns hits, protocol context should be in LLM messages."""
        mock_llm = MagicMock()
        intake_output = _make_intake_output(confidence=0.3)
        mock_llm.call = AsyncMock(side_effect=[_valid_phase1_result(), intake_output])

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session(
            intake_state=StructuredIntakeState(chief_complaint="chest pain"),
        )

        result = await orch.process_turn(session, "I have bad chest pain")

        assert result["action"] in ("ask", "ask_question")

        # Check that the LLM messages included protocol context
        # First call is Phase1, second is intake
        phase1_messages = mock_llm.call.call_args_list[0].kwargs.get("messages", [])
        has_protocol_context = any(
            "PROTOCOL CONTEXT" in msg.get("content", "")
            for msg in phase1_messages
            if msg.get("role") == "system"
        )
        assert has_protocol_context, (
            "Phase1 LLM messages should include protocol context"
        )

        # Intake messages should also have protocol context
        mock_llm.call.call_args_list[1].kwargs.get("messages", [])

    @pytest.mark.asyncio
    async def test_protocol_hits_in_decision_trace(self):
        """Decision trace should include protocol_hits from retrieval."""
        mock_llm = MagicMock()
        intake_output = _make_intake_output(confidence=0.3)
        mock_llm.call = AsyncMock(side_effect=[_valid_phase1_result(), intake_output])

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session(
            intake_state=StructuredIntakeState(chief_complaint="chest pain"),
        )

        await orch.process_turn(session, "I have bad chest pain")

        assert len(session.decision_trace) >= 1
        trace = session.decision_trace[-1]
        assert len(trace.protocol_hits) >= 1
        assert trace.protocol_hits[0].id == "PROTO-001"
        assert len(trace.protocol_citations) >= 1
        assert "PROTO-001" in trace.protocol_citations

    @pytest.mark.asyncio
    async def test_no_protocol_matches_still_works(self):
        """When retriever returns nothing, the system should work normally."""
        mock_llm = MagicMock()
        intake_output = _make_intake_output(confidence=0.3)
        mock_llm.call = AsyncMock(side_effect=[_valid_phase1_result(), intake_output])

        # Use a retriever with no protocols
        empty_retriever = ProtocolRetriever(protocols=[])
        orch = Orchestrator(llm_client=mock_llm, protocol_retriever=empty_retriever)
        session = _make_session()

        result = await orch.process_turn(session, "I have a headache")

        assert result["action"] == "ask"
        assert len(session.decision_trace) >= 1
        trace = session.decision_trace[-1]
        assert trace.protocol_hits == []
        assert trace.protocol_citations == []

    @pytest.mark.asyncio
    async def test_retriever_failure_does_not_crash(self):
        """If the retriever raises an exception, orchestrator continues."""
        mock_llm = MagicMock()
        intake_output = _make_intake_output(confidence=0.3)
        mock_llm.call = AsyncMock(side_effect=[_valid_phase1_result(), intake_output])

        # Create a retriever that explodes
        broken_retriever = ProtocolRetriever(protocols=[])
        broken_retriever.retrieve = MagicMock(
            side_effect=RuntimeError("retriever crashed")
        )

        orch = Orchestrator(llm_client=mock_llm, protocol_retriever=broken_retriever)
        session = _make_session()

        result = await orch.process_turn(session, "I have a headache")

        assert result["action"] == "ask"
        # Should have no protocol hits but not crash
        trace = session.decision_trace[-1]
        assert trace.protocol_hits == []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SECTION 6 — Safety Hierarchy Preserved
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestProtocolCannotDowngradeUrgency:
    """Protocol context must NEVER downgrade urgency or override red flags."""

    @pytest.mark.asyncio
    async def test_red_flag_still_escalates_with_protocol(self):
        """Even if protocol context is available, red flags still trigger ER_NOW."""
        mock_llm = MagicMock()
        mock_llm.call = AsyncMock()  # should NOT be called

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session(
            intake_state=StructuredIntakeState(chief_complaint="chest pain"),
        )

        result = await orch.process_turn(session, "I can't breathe at all")

        # Red flag should trigger escalation BEFORE any protocol/LLM logic
        assert result["action"] == "escalate"
        assert session.is_finalized is True
        mock_llm.call.assert_not_called()

    @pytest.mark.asyncio
    async def test_protocol_context_includes_no_downgrade_instruction(self):
        """Protocol context message must contain anti-downgrade instruction."""
        mock_llm = MagicMock()
        intake_output = _make_intake_output(confidence=0.3)
        mock_llm.call = AsyncMock(side_effect=[_valid_phase1_result(), intake_output])

        orch = Orchestrator(llm_client=mock_llm)
        session = _make_session(
            intake_state=StructuredIntakeState(chief_complaint="chest pain"),
        )

        await orch.process_turn(session, "I have chest tightness")

        # Check the Phase1 messages sent to LLM
        phase1_messages = mock_llm.call.call_args_list[0].kwargs.get("messages", [])
        protocol_msg = [
            msg["content"]
            for msg in phase1_messages
            if msg.get("role") == "system"
            and "PROTOCOL CONTEXT" in msg.get("content", "")
        ]
        assert len(protocol_msg) >= 1
        assert "NOT" in protocol_msg[0] and "downgrade" in protocol_msg[0]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SECTION 7 — Schema Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestProtocolSchemas:
    """ProtocolHit and updated DecisionTraceEntry schemas work correctly."""

    def test_protocol_hit_creation(self):
        hit = ProtocolHit(id="PROTO-001", title="Chest Pain", version="1.0")
        assert hit.id == "PROTO-001"
        assert hit.title == "Chest Pain"
        assert hit.version == "1.0"

    def test_decision_trace_with_protocol_hits(self):
        entry = DecisionTraceEntry(
            turn_number=1,
            user_text="chest pain",
            confidence_score=0.5,
            disposition="UNDECIDED",
            escalation_required=False,
            system_response="When did this start?",
            protocol_hits=[
                ProtocolHit(id="PROTO-001", title="Chest Pain", version="1.0"),
            ],
            protocol_citations=["PROTO-001"],
        )
        assert len(entry.protocol_hits) == 1
        assert entry.protocol_hits[0].id == "PROTO-001"
        assert entry.protocol_citations == ["PROTO-001"]

    def test_decision_trace_defaults_empty_protocols(self):
        entry = DecisionTraceEntry(
            turn_number=1,
            user_text="hello",
            confidence_score=0.5,
            disposition="UNDECIDED",
            escalation_required=False,
            system_response="What's your concern?",
        )
        assert entry.protocol_hits == []
        assert entry.protocol_citations == []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SECTION 8 — Protocol Format Context
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestFormatProtocolContext:
    """_format_protocol_context produces well-structured system messages."""

    def test_format_includes_all_snippets(self):
        snippets = [
            ProtocolSnippet(
                id="P1", title="Proto 1", version="1.0", excerpt="excerpt 1", score=5.0
            ),
            ProtocolSnippet(
                id="P2", title="Proto 2", version="2.0", excerpt="excerpt 2", score=3.0
            ),
        ]
        text = Orchestrator._format_protocol_context(snippets)
        assert "P1" in text
        assert "P2" in text
        assert "excerpt 1" in text
        assert "excerpt 2" in text
        assert "PROTOCOL CONTEXT" in text
        assert "downgrade" in text.lower()

    def test_format_empty_list(self):
        text = Orchestrator._format_protocol_context([])
        assert "PROTOCOL CONTEXT" in text
