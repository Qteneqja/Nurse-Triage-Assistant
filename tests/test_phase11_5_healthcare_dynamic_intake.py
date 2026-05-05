from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks

from src.llm.client import LLMCallError
from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.schemas import (
    ConversationTurn,
    IntakeStatePatch,
    IntakeTurnOutput,
    OrchestratorSession,
    Phase1Disposition,
    Phase1NextAction,
    Phase1TurnOutput,
)
from src.platform.workflows.schemas import (
    ResolvedWorkflowRoute,
    WorkflowContext,
    WorkflowInput,
    WorkflowTurnResult,
)
from src.verticals.healthcare.completeness import (
    MIN_DYNAMIC_TURNS_BEFORE_ROUTINE_FINALIZE,
    evaluate_healthcare_intake_completeness,
)
from src.verticals.healthcare.constants import (
    HEALTHCARE_TRIAGE_VERSION,
    HEALTHCARE_TRIAGE_WORKFLOW_ID,
    HEALTHCARE_VERTICAL,
)
from src.verticals.healthcare.workflow import HealthcareTriageWorkflow


def _context(session_id: str = "hc-phase-11-5") -> WorkflowContext:
    return WorkflowContext(
        session_id=session_id,
        vertical=HEALTHCARE_VERTICAL,
        workflow_id=HEALTHCARE_TRIAGE_WORKFLOW_ID,
        workflow_version=HEALTHCARE_TRIAGE_VERSION,
        call_sid=f"CA-{session_id}",
    )


def _scripted_session(session_id: str = "hc-phase-11-5") -> OrchestratorSession:
    session = OrchestratorSession(
        session_id=session_id,
        call_sid=f"CA-{session_id}",
        vertical_key=HEALTHCARE_VERTICAL,
        workflow_id=HEALTHCARE_TRIAGE_WORKFLOW_ID,
        workflow_version=HEALTHCARE_TRIAGE_VERSION,
    )
    session.intake_state.caller_name = "Jane Doe"
    session.intake_state.caller_age = 35
    session.intake_state.caller_sex = "female"
    session.intake_state.chief_complaint = "mild cough"
    session.channel_metadata["stage"] = "DYNAMIC"
    return session


def _complete_healthcare_session(
    session_id: str = "hc-complete",
    *,
    turn_count: int = 0,
) -> OrchestratorSession:
    session = _scripted_session(session_id)
    session.turn_count = turn_count
    session.intake_state.chief_complaint = "mild cough"
    session.intake_state.onset_time = "two days ago"
    session.intake_state.symptom_severity = "mild"
    session.intake_state.relevant_history = ["no asthma or chronic lung disease"]
    return session


def _phase1(
    *,
    escalation_required: bool = False,
    disposition: Phase1Disposition = Phase1Disposition.HUMAN_REVIEW,
    red_flags: list[str] | None = None,
) -> Phase1TurnOutput:
    return Phase1TurnOutput(
        confidence_score=0.8,
        escalation_required=escalation_required,
        red_flags_triggered=red_flags or [],
        rules_triggered=[],
        next_action=Phase1NextAction.ESCALATE_HUMAN
        if escalation_required
        else Phase1NextAction.ASK_QUESTION,
        disposition=disposition,
    )


def _intake(
    *,
    patch: IntakeStatePatch | None = None,
    next_question: str = "When did this start?",
    missing: list[str] | None = None,
    confidence: float = 0.9,
    finalize_ready: bool = True,
) -> IntakeTurnOutput:
    return IntakeTurnOutput(
        extracted_fields_update=patch or IntakeStatePatch(),
        missing_fields_prioritized=["onset_time"] if missing is None else missing,
        next_question=next_question,
        confidence=confidence,
        finalize_ready=finalize_ready,
    )


@pytest.mark.asyncio
async def test_noncritical_healthcare_call_continues_after_two_symptom_answers():
    mock_llm = MagicMock()
    mock_llm.call = AsyncMock(
        side_effect=[
            _phase1(escalation_required=True),
            _intake(
                patch=IntakeStatePatch(onset_time="yesterday"),
                next_question="On a scale from 1 to 10, how severe is it right now?",
                missing=["symptom_severity", "associated_symptoms"],
            ),
            _phase1(escalation_required=True),
            _intake(
                patch=IntakeStatePatch(symptom_severity="mild"),
                next_question="Do you have fever, shortness of breath, or chest pain?",
                missing=["associated_symptoms"],
            ),
        ]
    )

    workflow = HealthcareTriageWorkflow(orchestrator=Orchestrator(llm_client=mock_llm))
    context = _context()
    first = await workflow.handle_turn(
        context,
        WorkflowInput(
            user_text="It started yesterday.",
            session_state=_scripted_session().model_dump(mode="json"),
        ),
    )
    second = await workflow.handle_turn(
        context,
        WorkflowInput(
            user_text="It is mild, about a 2.",
            session_state=first.updated_state,
        ),
    )

    assert first.should_continue is True
    assert first.escalation_required is False
    assert first.audit_metadata["trace_escalation_required"] is True
    assert first.audit_metadata["trace_escalation_suppressed"] is True
    assert second.should_continue is True
    assert second.should_finalize is False
    assert second.escalation_required is False
    completeness = second.audit_metadata["healthcare_intake_completeness"]
    assert completeness["is_complete"] is False
    assert completeness["missing_items"] == ["associated_symptoms_or_relevant_history"]
    assert completeness["minimum_dynamic_turns_met"] is False
    assert completeness["dynamic_turn_count"] == 2
    assert completeness["finalization_blocked_reason"] == "missing_clinical_items"
    assert (
        second.audit_metadata["healthcare_finalization_blocked_reason"]
        == "missing_clinical_items"
    )
    assert "nurse" not in second.assistant_text.lower()
    assert "connect" not in second.assistant_text.lower()


@pytest.mark.asyncio
async def test_red_flag_still_escalates_immediately_without_llm():
    mock_llm = MagicMock()
    mock_llm.call = AsyncMock()
    workflow = HealthcareTriageWorkflow(orchestrator=Orchestrator(llm_client=mock_llm))

    result = await workflow.handle_turn(
        _context("hc-red-flag"),
        WorkflowInput(
            user_text="I can't breathe at all.",
            session_state=_scripted_session("hc-red-flag").model_dump(mode="json"),
        ),
    )

    assert result.should_continue is False
    assert result.escalation_required is True
    assert result.recommended_disposition == "ER_NOW"
    assert result.audit_metadata["finalization_reason"] == "critical_red_flag"
    mock_llm.call.assert_not_called()


@pytest.mark.asyncio
async def test_repeated_unclear_caller_escalates_with_reason():
    mock_llm = MagicMock()
    mock_llm.call = AsyncMock()
    orch = Orchestrator(llm_client=mock_llm)
    session = _scripted_session("hc-unclear")

    assert (await orch.process_turn(session, "hmm"))["action"] == "ask"
    assert (await orch.process_turn(session, "uh"))["action"] == "ask"
    result = await orch.process_turn(session, "I don't know")

    assert result["action"] == "escalate"
    assert session.finalization_reason == "repeated_unclear_answers"
    assert session.decision_trace[-1].finalization_reason == (
        "repeated_unclear_answers"
    )
    mock_llm.call.assert_not_called()


@pytest.mark.asyncio
async def test_schema_llm_failure_falls_back_with_validation_reason():
    mock_llm = MagicMock()
    mock_llm.call = AsyncMock(
        side_effect=LLMCallError("JSON validation failed after repair attempt")
    )
    orch = Orchestrator(llm_client=mock_llm)
    session = _scripted_session("hc-llm-failure")

    result = await orch.process_turn(session, "I have a mild cough.")

    assert result["action"] == "escalate"
    assert session.finalization_reason == "llm_validation_failure"
    assert session.decision_trace[-1].finalization_reason == "llm_validation_failure"


@pytest.mark.asyncio
async def test_minimum_dynamic_turns_and_completeness_gate_blocks_finalize():
    session = _scripted_session("hc-incomplete")
    session.turn_count = 1
    completeness = evaluate_healthcare_intake_completeness(session)

    assert completeness.is_complete is False
    assert completeness.minimum_dynamic_turns_met is False
    assert "onset_duration" in completeness.missing_items
    assert "severity" in completeness.missing_items

    mock_llm = MagicMock()
    mock_llm.call = AsyncMock(
        side_effect=[
            _phase1(disposition=Phase1Disposition.SELF_CARE),
            _intake(
                next_question="When did this cough start?",
                missing=["onset_time", "symptom_severity"],
                confidence=0.95,
                finalize_ready=True,
            ),
        ]
    )
    orch = Orchestrator(llm_client=mock_llm)
    result = await orch.process_turn(session, "It is just a mild cough.")

    assert result["action"] == "ask"
    assert session.is_finalized is False
    assert (
        session.channel_metadata["healthcare_intake_completeness"]["is_complete"]
        is False
    )
    assert (
        session.channel_metadata["healthcare_intake_completeness"][
            "finalization_blocked_reason"
        ]
        == "missing_clinical_items"
    )
    assert (
        session.channel_metadata["healthcare_finalization_blocked_reason"]
        == "missing_clinical_items"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("confidence", "finalize_ready", "missing"),
    [
        (0.99, False, ["medications"]),
        (0.60, True, ["medications"]),
        (0.60, False, []),
    ],
)
async def test_llm_soft_finalize_signals_do_not_override_minimum_dynamic_turns(
    confidence,
    finalize_ready,
    missing,
):
    session = _complete_healthcare_session("hc-soft-signal-blocked", turn_count=1)

    completeness = evaluate_healthcare_intake_completeness(session)
    assert completeness.is_complete is False
    assert completeness.minimum_dynamic_turns_met is False
    assert completeness.missing_items == []
    assert completeness.reason == "minimum_dynamic_turns_not_met"

    mock_llm = MagicMock()
    mock_llm.call = AsyncMock(
        side_effect=[
            _phase1(disposition=Phase1Disposition.SELF_CARE),
            _intake(
                patch=IntakeStatePatch(notes="No fever or breathing trouble reported."),
                next_question="Do you have any other symptoms or medical history?",
                missing=missing,
                confidence=confidence,
                finalize_ready=finalize_ready,
            ),
        ]
    )
    orch = Orchestrator(llm_client=mock_llm)
    result = await orch.process_turn(session, "No fever or breathing trouble.")

    assert result["action"] == "ask"
    assert session.is_finalized is False
    completeness_payload = session.channel_metadata["healthcare_intake_completeness"]
    assert completeness_payload["is_complete"] is False
    assert completeness_payload["missing_items"] == []
    assert completeness_payload["minimum_dynamic_turns_met"] is False
    assert (
        completeness_payload["finalization_blocked_reason"]
        == "minimum_dynamic_turns_not_met"
    )


@pytest.mark.asyncio
async def test_sufficient_information_finalizes_with_reason_after_hard_gates():
    session = _complete_healthcare_session("hc-complete", turn_count=6)

    mock_llm = MagicMock()
    mock_llm.call = AsyncMock(
        side_effect=[
            _phase1(disposition=Phase1Disposition.SELF_CARE),
            _intake(
                patch=IntakeStatePatch(notes="No fever or breathing trouble reported."),
                next_question="Is there anything else important I should know?",
                missing=[],
                confidence=0.95,
                finalize_ready=True,
            ),
        ]
    )
    orch = Orchestrator(llm_client=mock_llm)
    result = await orch.process_turn(session, "No fever or breathing trouble.")

    assert result["action"] == "finalize"
    assert session.finalization_reason == "sufficient_information"
    assert session.decision_trace[-1].finalization_reason == "sufficient_information"
    completeness_payload = session.channel_metadata["healthcare_intake_completeness"]
    assert completeness_payload["is_complete"] is True
    assert completeness_payload["missing_items"] == []
    assert completeness_payload["minimum_dynamic_turns_met"] is True
    assert completeness_payload["dynamic_turn_count"] >= (
        MIN_DYNAMIC_TURNS_BEFORE_ROUTINE_FINALIZE
    )
    assert completeness_payload["finalization_blocked_reason"] is None


@pytest.mark.asyncio
async def test_twilio_keeps_gathering_after_two_usable_dynamic_responses():
    from src.storage.factory import reset_storage_backend
    from src.storage.session_repository import (
        get_session_repository,
        reset_session_repository,
    )
    from src.twilio import routes as twilio_routes
    from src.verticals.healthcare.workflow import HealthcareTriageWorkflow

    with (
        patch("src.config.STORAGE_BACKEND", "memory"),
        patch("src.config.ENVIRONMENT", "development"),
        patch("src.config.DATABASE_URL", None),
        patch("src.twilio.routes.text_to_speech_url", new=AsyncMock(return_value=None)),
    ):
        reset_session_repository()
        reset_storage_backend()
        twilio_routes._pending_turns.clear()
        repo = get_session_repository()
        session = repo.create_session(
            call_sid="CA-HC-PHONE-REGRESSION",
            workflow_route=ResolvedWorkflowRoute(
                vertical_key=HEALTHCARE_VERTICAL,
                workflow_id=HEALTHCARE_TRIAGE_WORKFLOW_ID,
                workflow_version=HEALTHCARE_TRIAGE_VERSION,
            ),
        )
        intake = HealthcareTriageWorkflow().get_scripted_intake_definition()
        twilio_routes._initialize_scripted_intake(session, intake)
        repo.persist_session(session)

        request = SimpleNamespace(headers={})
        for speech in ["Jane Doe", "35", "female"]:
            await twilio_routes.handle_gather(
                request,
                BackgroundTasks(),
                CallSid="CA-HC-PHONE-REGRESSION",
                SpeechResult=speech,
            )

        engine = MagicMock()
        questions = [
            "Thanks. I need to ask a few more questions to understand the urgency. When did this start?",
            "On a scale from 1 to 10, how severe is it right now?",
            "Do you have fever, shortness of breath, or chest pain?",
        ]

        async def _handle_turn(context, workflow_input):
            updated = OrchestratorSession.model_validate(workflow_input.session_state)
            updated.turn_count += 1
            updated.channel_metadata["stage"] = twilio_routes.STAGE_DYNAMIC
            user_text = workflow_input.user_text.lower()
            if "cough" in user_text:
                updated.intake_state.chief_complaint = "mild cough"
            elif "yesterday" in user_text:
                updated.intake_state.onset_time = "yesterday"
            elif "mild" in user_text:
                updated.intake_state.symptom_severity = "mild"
            assistant_text = questions[min(updated.turn_count - 1, len(questions) - 1)]
            updated.conversation.append(
                ConversationTurn(role="assistant", text=assistant_text)
            )
            return WorkflowTurnResult(
                assistant_text=assistant_text,
                stage=twilio_routes.STAGE_DYNAMIC,
                should_continue=True,
                should_finalize=False,
                escalation_required=False,
                recommended_disposition="SELF_CARE",
                confidence_score=0.85,
                updated_state=updated.model_dump(mode="json"),
                audit_metadata={
                    "finalization_reason": None,
                    "trace_escalation_suppressed": False,
                },
            )

        engine.handle_turn = AsyncMock(side_effect=_handle_turn)

        async def _dynamic_turn(speech: str) -> str:
            response = await twilio_routes.handle_gather(
                request,
                BackgroundTasks(),
                CallSid="CA-HC-PHONE-REGRESSION",
                SpeechResult=speech,
            )
            assert "/api/v1/voice/thinking" in response.body.decode()
            task, _ = twilio_routes._pending_turns["CA-HC-PHONE-REGRESSION"]
            await task
            thinking_response = await twilio_routes.handle_thinking(
                request,
                BackgroundTasks(),
                CallSid="CA-HC-PHONE-REGRESSION",
            )
            return thinking_response.body.decode()

        with patch("src.twilio.routes.get_workflow_engine", return_value=engine):
            await _dynamic_turn("I have a mild cough.")
            second_body = await _dynamic_turn("It started yesterday.")
            body = await _dynamic_turn("It is mild, about two out of ten.")

    second_lower_body = second_body.lower()
    assert "<gather" in second_lower_body
    assert "nurse" not in second_lower_body
    assert "connecting" not in second_lower_body

    lower_body = body.lower()
    assert "<gather" in lower_body
    assert "nurse" not in lower_body
    assert "connecting" not in lower_body
    assert engine.handle_turn.await_count == 3

    second_input = engine.handle_turn.await_args_list[1].args[1]
    third_input = engine.handle_turn.await_args_list[2].args[1]
    assert second_input.session_state["turn_count"] == 1
    assert third_input.session_state["turn_count"] == 2
    assert third_input.session_state["unclear_answer_retries"] == 0
    prior_text = " ".join(
        turn["text"] for turn in third_input.session_state["conversation"]
    ).lower()
    assert "mild cough" in prior_text
    assert "yesterday" in prior_text
