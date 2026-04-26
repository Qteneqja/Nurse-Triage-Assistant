import logging

from src.platform.extraction.base import BaseExtractionAgent
from src.platform.extraction.service import ExtractionService
from src.platform.extraction.schemas import ExtractionResult
from src.platform.workflows.schemas import WorkflowContext, WorkflowFinalResult
from src.storage.memory import InMemoryOrchestratorStorage
from src.verticals.healthcare.extraction import HealthcareExtractionAgent


def _context() -> WorkflowContext:
    return WorkflowContext(
        session_id="extract-session",
        organization_id="org-1",
        vertical="healthcare",
        workflow_id="healthcare_triage_v1",
        workflow_version="v1",
    )


def _final(disposition: str = "URGENT") -> WorkflowFinalResult:
    return WorkflowFinalResult(
        final_disposition=disposition,
        confidence_score=0.82,
        summary="Patient completed triage.",
        structured_output={
            "output_type": "SBAR",
            "sbar_report": "S: Test\nB: Test\nA: Test\nR: Test",
            "intake_state": {
                "chief_complaint": "chest pain",
                "caller_age": 55,
                "caller_sex": "male",
            },
        },
        safety_events=[{"flag": "chest_pain_high_risk"}],
        rules_triggered=["pre_check:score=10"],
    )


def test_healthcare_extraction_produces_structured_json():
    result = HealthcareExtractionAgent().extract(
        transcript=[{"role": "caller", "text": "I have chest pain"}],
        final_result=_final(),
        workflow_context=_context(),
        extraction_schema={"schema_version": "healthcare_triage_extraction_v1"},
    )

    assert result.vertical == "healthcare"
    assert result.workflow_id == "healthcare_triage_v1"
    assert result.entities["chief_complaint"] == "chest pain"
    assert result.entities["final_disposition"] == "URGENT"
    assert result.metrics["sbar_available"] is True
    assert "clinical_follow_up_required" in result.recommended_actions


def test_extraction_does_not_alter_disposition():
    service = ExtractionService()
    service.register("healthcare_triage_v1", HealthcareExtractionAgent())
    final_result = _final(disposition="SCHEDULE")

    extraction = service.extract(
        transcript=[],
        final_result=final_result,
        workflow_context=_context(),
        extraction_schema=None,
    )

    assert final_result.final_disposition == "SCHEDULE"
    assert extraction.entities["final_disposition"] == "SCHEDULE"


def test_missing_optional_fields_do_not_crash_extraction():
    sparse = WorkflowFinalResult(
        final_disposition="HUMAN_REVIEW",
        confidence_score=0.4,
        summary="Manual review required.",
        structured_output={},
    )

    result = HealthcareExtractionAgent().extract(
        transcript="",
        final_result=sparse,
        workflow_context=_context(),
        extraction_schema=None,
    )

    assert result.entities["final_disposition"] == "HUMAN_REVIEW"
    assert result.metrics["sbar_available"] is False


def test_extraction_result_persists_when_storage_supports_it():
    storage = InMemoryOrchestratorStorage()
    session = storage.create_session(call_sid="CALL-EXTRACT")
    result = HealthcareExtractionAgent().extract(
        transcript=[],
        final_result=_final(),
        workflow_context=WorkflowContext(
            session_id=session.session_id,
            vertical="healthcare",
            workflow_id="healthcare_triage_v1",
            workflow_version="v1",
        ),
        extraction_schema=None,
    )

    storage.save_extraction(result)

    saved = storage.get_extractions(session.session_id)
    assert len(saved) == 1
    assert saved[0].entities["final_disposition"] == "URGENT"


def test_extract_and_persist_failure_returns_safe_result_and_logs(caplog):
    class BrokenExtractionAgent(BaseExtractionAgent):
        def extract(
            self,
            transcript,
            final_result,
            workflow_context,
            extraction_schema,
        ) -> ExtractionResult:
            final_result.final_disposition = "SCHEDULE"
            raise RuntimeError("contains sensitive transcript? no")

    service = ExtractionService()
    service.register("healthcare_triage_v1", BrokenExtractionAgent())
    final_result = _final(disposition="URGENT")

    with caplog.at_level(logging.WARNING):
        extraction = service.extract_and_persist(
            transcript=[{"role": "caller", "text": "Name Jane Doe"}],
            final_result=final_result,
            workflow_context=_context(),
            extraction_schema=None,
        )

    assert final_result.final_disposition == "URGENT"
    assert extraction.schema_version == "extraction_failed_v1"
    assert extraction.entities["final_disposition"] == "URGENT"
    assert "extraction_failed" in extraction.flags
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "Name Jane Doe" not in logged
    assert "contains sensitive transcript" not in logged


def test_healthcare_extraction_handles_partial_session_data():
    partial = WorkflowFinalResult(
        final_disposition="ER_NOW",
        confidence_score=0.2,
        summary="Emergency escalation.",
        structured_output={"intake_state": {"chief_complaint": "breathing trouble"}},
        safety_events=[{}],
        rules_triggered=[],
    )

    result = HealthcareExtractionAgent().extract(
        transcript=[{"role": "assistant", "text": "Call emergency services."}],
        final_result=partial,
        workflow_context=_context(),
        extraction_schema=None,
    )

    assert result.entities["chief_complaint"] == "breathing trouble"
    assert result.entities["final_disposition"] == "ER_NOW"
    assert result.metrics["sbar_available"] is False
