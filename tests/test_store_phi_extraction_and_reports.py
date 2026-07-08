"""STORE_PHI=false must cover the last two at-rest sinks (PR #91 follow-up).

* conversation_extractions rows — summary, entities_json,
  recommended_actions_json get the session-blob redaction treatment;
  raw_output_json (verbatim LLM output, unmaskable) is dropped;
  STORE_PHI=true byte-identical.
* healthcare report FILES (local reports/ dir + Azure Blob twin) — the
  structured JSON gets the blob redaction, the SBAR text keeps mask_phi
  strengthened with the captured-identity literal scrub, and the filename
  no longer embeds the patient name.

All assertions run against real stored output (SQLite round-trip / real
files on disk), not mocks of the code under test.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.orchestrator.schemas import (
    DispositionCategory,
    FinalizeOutput,
    OrchestratorSession,
    SafetyFlag,
    SafetyLevel,
)
from src.platform.extraction.schemas import ExtractionResult
from src.storage.models import Base, ConversationExtractionModel
from src.twilio import routes as rt

# Planted PII — fabricated test values only.
NAME = "Marlene Vandersloot"
PHONE = "204-555-0177"
EMAIL = "marlene.vandersloot@example.com"
PLATE = "TST 951"


@pytest.fixture
def storage():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    from src.storage.postgres import PostgresStorage

    backend = PostgresStorage.__new__(PostgresStorage)
    backend._engine = engine
    backend._SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
    return backend


def _extraction() -> ExtractionResult:
    return ExtractionResult(
        session_id="ext-test",
        vertical="automotive_collision",
        workflow_id="birchwood_collision_intake_v1",
        schema_version="test_v1",
        summary=f"Caller {NAME} at {PHONE} reported a scrape; email {EMAIL}.",
        entities={
            "caller_name": NAME,
            "phone": PHONE,
            "email": EMAIL,
            "license_plate": PLATE,
            "vehicle_make": "Toyota",
        },
        metrics={"turns": 6},
        # A machine slug and an LLM-composed flag describing the caller.
        flags=["missing_claim_number", f"caller {NAME} reports injury, call {PHONE}"],
        recommended_actions=[f"Call {NAME} back at {PHONE} to confirm booking."],
        confidence_score=0.9,
        raw_model_output={"verbatim": f"name={NAME} phone={PHONE}"},
    )


def _stored_row(storage) -> ConversationExtractionModel:
    with storage._SessionFactory() as db:
        return db.execute(select(ConversationExtractionModel)).scalar_one()


# ---------------------------------------------------------------------------
# Sink 1 — conversation_extractions rows
# ---------------------------------------------------------------------------


def test_extraction_row_contains_no_unmasked_phi(storage, monkeypatch):
    monkeypatch.setattr("src.storage.postgres.STORE_PHI", False)
    storage.save_extraction(_extraction())

    row = _stored_row(storage)
    serialized = json.dumps(
        {
            "summary": row.summary,
            "entities": row.entities_json,
            "recommended_actions": row.recommended_actions_json,
            "raw_output": row.raw_output_json,
            "metrics": row.metrics_json,
            "flags": row.flags_json,
        }
    )
    for pii in (NAME, PHONE, EMAIL, PLATE):
        assert pii not in serialized, pii
    # Identity entities fully redacted; operational facts survive.
    assert row.entities_json["caller_name"] == "[REDACTED]"
    assert row.entities_json["vehicle_make"] == "Toyota"
    # Verbatim LLM output cannot be reliably masked — dropped at rest.
    assert row.raw_output_json is None
    # Non-PHI analytics unchanged; machine flag slugs survive, LLM flag
    # text is masked.
    assert row.metrics_json == {"turns": 6}
    assert row.flags_json[0] == "missing_claim_number"
    assert NAME not in row.flags_json[1] and PHONE not in row.flags_json[1]


def test_extraction_row_store_phi_true_unchanged(storage, monkeypatch):
    monkeypatch.setattr("src.storage.postgres.STORE_PHI", True)
    extraction = _extraction()
    storage.save_extraction(extraction)

    row = _stored_row(storage)
    assert row.summary == extraction.summary
    assert row.entities_json == extraction.entities
    assert row.recommended_actions_json == extraction.recommended_actions
    assert row.flags_json == extraction.flags
    assert row.raw_output_json == extraction.raw_model_output


# ---------------------------------------------------------------------------
# Sink 2 — healthcare report files
# ---------------------------------------------------------------------------


class _StubRepo:
    def persist_session(self, session):
        pass


def _healthcare_session() -> OrchestratorSession:
    session = OrchestratorSession(session_id="report-test", call_sid="CA-report")
    session.workflow_id = "healthcare_triage_v1"
    session.intake_state.caller_name = NAME
    session.intake_state.chief_complaint = f"chest tightness, callback {PHONE}"
    session.intake_state.location = "123 Main Street"
    session.audit_trace.add_entry(
        step="turn",
        agent="orchestrator",
        input_summary=f"Turn 1: my name is {NAME}, number {PHONE}",
    )
    session.safety_flags.append(
        SafetyFlag(
            source="llm",
            level=SafetyLevel.ADVISORY,
            flag=f"caller {NAME} reports chest tightness, lives alone",
            reason_for_audit=f"LLM-detected: caller {NAME} reports chest tightness",
        )
    )
    session.finalize_output = FinalizeOutput(
        disposition=DispositionCategory.SELF_CARE,
        disposition_reasoning="Mild symptoms, no red flags.",
        sbar_report=(
            f"S: {NAME} reports mild chest tightness. "
            f"B: reachable at {PHONE}. A: low risk. R: self care."
        ),
        safety_net_instructions=[f"If symptoms worsen, we will call {NAME} back."],
    )
    session.is_finalized = True
    return session


def _run_report(tmp_path, monkeypatch, store_phi: bool):
    monkeypatch.setattr(rt, "STORE_PHI", store_phi)
    monkeypatch.setattr(rt, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(rt, "upload_reports_to_blob", lambda *a, **k: {})
    monkeypatch.setattr(rt, "_run_post_call_extraction", lambda *a, **k: None)
    monkeypatch.setattr(rt, "get_session_repository", lambda: _StubRepo())

    session = _healthcare_session()
    asyncio.run(
        rt._generate_orchestrator_report_background(
            session_id="report-test",
            orch_session=session,
            session_metadata={
                "patient_name": NAME,
                "patient_age": "44",
                "patient_sex": "female",
                "chief_complaint": session.intake_state.chief_complaint,
            },
        )
    )
    json_files = list(tmp_path.rglob("*.json"))
    txt_files = list(tmp_path.rglob("*.txt"))
    assert len(json_files) == 1 and len(txt_files) == 1
    return json_files[0], txt_files[0]


def test_report_files_redacted_when_store_phi_false(tmp_path, monkeypatch):
    json_path, txt_path = _run_report(tmp_path, monkeypatch, store_phi=False)

    # Filename no longer embeds the patient name (was on disk + in blob URL).
    assert NAME.split()[0] not in json_path.name
    assert "Unknown" in json_path.name

    json_text = json_path.read_text()
    txt_text = txt_path.read_text()
    for pii in (NAME, PHONE, "123 Main Street"):
        assert pii not in json_text, pii
        assert pii not in txt_text, pii
    # The structured report is still a usable JSON document.
    structured = json.loads(json_text)
    assert structured["patient"]["name"] == "[REDACTED]"
    assert structured["disposition"]["level"] == "SELF_CARE"


def test_unknown_name_sentinel_does_not_shred_the_word_unknown(tmp_path, monkeypatch):
    """When no caller name was captured, patient.name is the sentinel
    "Unknown" — it must not become a scrub literal (which would rewrite the
    routine clinical word "unknown" everywhere) and must survive as the
    honest "we don't know" signal, not [REDACTED]."""
    monkeypatch.setattr(rt, "STORE_PHI", False)
    monkeypatch.setattr(rt, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(rt, "upload_reports_to_blob", lambda *a, **k: {})
    monkeypatch.setattr(rt, "_run_post_call_extraction", lambda *a, **k: None)
    monkeypatch.setattr(rt, "get_session_repository", lambda: _StubRepo())

    session = OrchestratorSession(session_id="anon-test", call_sid="CA-anon")
    session.workflow_id = "healthcare_triage_v1"
    session.intake_state.chief_complaint = "headache, onset unknown"
    session.finalize_output = FinalizeOutput(
        disposition=DispositionCategory.HUMAN_REVIEW,
        disposition_reasoning="Cause of symptoms unknown, escalating for review.",
    )
    session.is_finalized = True
    asyncio.run(
        rt._generate_orchestrator_report_background(
            session_id="anon-test",
            orch_session=session,
            session_metadata={
                "patient_name": "Unknown",
                "patient_age": "Unknown",
                "patient_sex": "Unknown",
                "chief_complaint": session.intake_state.chief_complaint,
            },
        )
    )
    structured = json.loads(next(tmp_path.rglob("*.json")).read_text())
    assert structured["patient"]["name"] == "Unknown"
    assert "unknown" in structured["disposition"]["reasoning"]
    assert "unknown" in structured["chief_complaint"]


def test_report_files_full_detail_when_store_phi_true(tmp_path, monkeypatch):
    json_path, txt_path = _run_report(tmp_path, monkeypatch, store_phi=True)

    # Existing behavior byte-for-byte: name in filename and full PHI content.
    assert "Marlene-Vandersloot" in json_path.name
    assert NAME in json_path.read_text()
    assert NAME in txt_path.read_text()
    assert PHONE in txt_path.read_text()
