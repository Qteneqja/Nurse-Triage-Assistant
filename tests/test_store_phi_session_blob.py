"""STORE_PHI=false must cover the serialized session blob (metadata_json).

Before this fix, STORE_PHI=false masked only the triage_turns text columns;
the caller's name, callback phone, email, plate, transcript and extraction
entities all still persisted raw inside metadata_json["session_state"] (and
raw entities inside the turn rows). These tests assert on the REAL serialized
output written through PostgresStorage (SQLite in-memory engine, same
fixture pattern as test_phase3_storage), not on a mock:

* finalized + STORE_PHI=false -> the written blob contains no unmasked
  caller identifiers, no raw entities, no transcript/turn text PII;
* the redacted blob still rehydrates (model_validate) — webhook redelivery
  after finalize must not break;
* ACTIVE sessions are written raw on purpose (the blob is the live call's
  working memory; masking it would destroy the intake mid-call) — asserted
  so nobody "fixes" this into a call-killing bug;
* STORE_PHI=true persistence is byte-identical to model_dump (unchanged).
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.orchestrator.schemas import (
    ConversationTurn,
    DecisionTraceEntry,
    OrchestratorSession,
)
from src.storage.models import Base, TriageSessionModel, TriageTurnModel
from src.storage.state_redaction import redact_session_state

# Planted PII — fabricated test values only.
NAME = "Johnathan Smithers"
PHONE = "204-555-0142"
EMAIL = "johnathan.smithers@example.com"
PLATE = "TST 482"
CALLER_TEXT = (
    f"my name is {NAME} and my number is {PHONE}, email {EMAIL}. "
    "I hit a pole and scraped the passenger side."
)


@pytest.fixture
def storage(monkeypatch):
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    from src.storage.postgres import PostgresStorage

    backend = PostgresStorage.__new__(PostgresStorage)
    backend._engine = engine
    backend._SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
    return backend


def _session_with_pii(finalized: bool) -> OrchestratorSession:
    session = OrchestratorSession(session_id="phi-test", call_sid="CA-phi-test")
    session.workflow_id = "birchwood_collision_intake_v1"
    session.conversation.append(ConversationTurn(role="caller", text=CALLER_TEXT))
    session.conversation.append(
        ConversationTurn(role="assistant", text="Thanks, I have all of that noted.")
    )
    session.channel_metadata["scripted_intake"] = {
        "fields": {
            "caller_name": NAME,
            "phone": PHONE,
            "email": EMAIL,
            "license_plate": PLATE,
            "vehicle_make": "Toyota",
            "incident_description": "hit a pole, passenger side scrape",
        },
        "completed": True,
    }
    session.decision_trace.append(
        DecisionTraceEntry(
            turn_number=1,
            user_text=CALLER_TEXT,
            extracted_entities={
                "caller_name": NAME,
                "phone": PHONE,
                "vehicle_make": "Toyota",
            },
            confidence_score=0.9,
            disposition="COMPLETED_INTAKE",
            escalation_required=False,
            system_response=f"Thanks {NAME}, we'll call you at {PHONE}.",
        )
    )
    session.is_finalized = finalized
    return session


def _raw_blob(storage) -> dict:
    with storage._SessionFactory() as db:
        row = db.execute(select(TriageSessionModel)).scalar_one()
        return row.metadata_json


def _assert_no_pii(serialized: str):
    assert NAME not in serialized
    assert PHONE not in serialized
    assert PHONE.replace("-", " ") not in serialized
    assert EMAIL not in serialized
    assert PLATE not in serialized


# ---------------------------------------------------------------------------
# The real serialized blob, STORE_PHI=false
# ---------------------------------------------------------------------------


def test_finalized_blob_contains_no_unmasked_phi(storage, monkeypatch):
    monkeypatch.setattr("src.storage.postgres.STORE_PHI", False)
    storage.save_session(_session_with_pii(finalized=True))

    blob = _raw_blob(storage)
    serialized = json.dumps(blob)
    _assert_no_pii(serialized)
    # Non-identifying operational facts survive for the shop.
    assert "Toyota" in serialized
    # CallSid is retained by design (opaque Twilio id, webhook correlation).
    assert blob["session_state"]["call_sid"] == "CA-phi-test"


def test_finalized_turn_rows_mask_text_and_entities(storage, monkeypatch):
    monkeypatch.setattr("src.storage.postgres.STORE_PHI", False)
    storage.save_session(_session_with_pii(finalized=True))

    with storage._SessionFactory() as db:
        turn = db.execute(select(TriageTurnModel)).scalar_one()
        assert turn.phi_masked is True
        _assert_no_pii(
            json.dumps(
                {
                    "user_text": turn.user_text,
                    "system_text": turn.system_text,
                    "entities": turn.extracted_entities,
                }
            )
        )
        assert turn.extracted_entities["caller_name"] == "[REDACTED]"
        assert turn.extracted_entities["vehicle_make"] == "Toyota"


def test_redacted_blob_still_rehydrates(storage, monkeypatch):
    monkeypatch.setattr("src.storage.postgres.STORE_PHI", False)
    storage.save_session(_session_with_pii(finalized=True))

    # Webhook redelivery after finalize rehydrates the redacted blob.
    loaded = storage.load_session_by_call("CA-phi-test")
    assert isinstance(loaded, OrchestratorSession)
    assert loaded.is_finalized is True
    assert loaded.channel_metadata["scripted_intake"]["fields"]["caller_name"] == (
        "[REDACTED]"
    )


# ---------------------------------------------------------------------------
# What must NOT change
# ---------------------------------------------------------------------------


def test_active_session_blob_is_not_redacted(storage, monkeypatch):
    """The blob is the live call's working memory — an in-flight session must
    round-trip its raw state or the intake is destroyed mid-call."""
    monkeypatch.setattr("src.storage.postgres.STORE_PHI", False)
    storage.save_session(_session_with_pii(finalized=False))

    loaded = storage.load_session_by_call("CA-phi-test")
    fields = loaded.channel_metadata["scripted_intake"]["fields"]
    assert fields["caller_name"] == NAME
    assert fields["phone"] == PHONE
    assert loaded.conversation[0].text == CALLER_TEXT


def test_store_phi_true_persists_full_state_unchanged(storage, monkeypatch):
    monkeypatch.setattr("src.storage.postgres.STORE_PHI", True)
    session = _session_with_pii(finalized=True)
    storage.save_session(session)

    blob = _raw_blob(storage)
    assert blob["session_state"] == session.model_dump(mode="json")
    with storage._SessionFactory() as db:
        turn = db.execute(select(TriageTurnModel)).scalar_one()
        assert turn.phi_masked is False
        assert turn.user_text == CALLER_TEXT
        assert turn.extracted_entities["caller_name"] == NAME


# ---------------------------------------------------------------------------
# Redaction walk unit behavior
# ---------------------------------------------------------------------------


def test_identity_keys_redact_bare_values_regex_cannot_catch():
    state = {"fields": {"caller_name": "Bare Name", "phone": "5551234567"}}
    redacted = redact_session_state(state)
    assert redacted["fields"]["caller_name"] == "[REDACTED]"
    assert redacted["fields"]["phone"] == "[REDACTED]"


def test_entity_containers_mask_every_string_value():
    state = {"extracted_entities": {"unanticipated_key": f"call {PHONE} today"}}
    redacted = redact_session_state(state)
    assert PHONE not in redacted["extracted_entities"]["unanticipated_key"]


def test_identity_subtree_is_force_redacted():
    state = {"phone": {"raw": "204-555-0142", "kind": "mobile"}}
    redacted = redact_session_state(state)
    assert redacted["phone"] == {"raw": "[REDACTED]", "kind": "[REDACTED]"}


def test_machine_state_survives_redaction():
    state = {
        "created_at": "2026-07-08T12:34:56+00:00",
        "stage": "FINAL",
        "confidence_score": 0.9,
        "last_gather": {"twiml": "<Response><Gather/></Response>"},
    }
    redacted = redact_session_state(state)
    assert redacted == state


def test_redacted_model_dump_validates():
    session = _session_with_pii(finalized=True)
    redacted = redact_session_state(session.model_dump(mode="json"))
    revived = OrchestratorSession.model_validate(redacted)
    assert revived.session_id == "phi-test"


# ---------------------------------------------------------------------------
# Security-review regression tests
# ---------------------------------------------------------------------------


def test_audit_trace_and_narrative_fields_are_masked():
    """input_summary carries the VERBATIM caller utterance (the orchestrator's
    _redact only truncates); SBAR/intake narrative fields carry spoken PHI in
    formats the identity-literal scrub can't know. All must go through
    mask_phi in the at-rest blob."""
    from src.orchestrator.schemas import AuditTrace

    session = _session_with_pii(finalized=True)
    session.audit_trace = AuditTrace(session_id="phi-test", call_sid="CA-phi-test")
    # Spaced phone format differs from the captured dashed field, and the
    # address was never captured under any identity key.
    session.audit_trace.add_entry(
        step="turn",
        agent="orchestrator",
        input_summary="Turn 1: my number is 204 555 0142, I live at 123 Main Street",
    )
    session.intake_state.location = "123 Main Street"

    redacted = redact_session_state(session.model_dump(mode="json"))
    serialized = json.dumps(redacted)
    assert "204 555 0142" not in serialized
    assert "123 Main Street" not in serialized
    # Still rehydrates.
    OrchestratorSession.model_validate(redacted)


def test_caller_dictated_values_cannot_corrupt_machine_state():
    """A caller truthfully named 'Birchwood' (or a plate reading 'URGENT')
    must not let the literal scrub rewrite workflow ids, dispositions, or any
    other machine-state string in the persisted record."""
    session = _session_with_pii(finalized=True)
    fields = session.channel_metadata["scripted_intake"]["fields"]
    fields["caller_name"] = "Birchwood"
    fields["license_plate"] = "URGENT"
    session.decision_trace[0].disposition = "URGENT"

    redacted = redact_session_state(session.model_dump(mode="json"))
    assert redacted["workflow_id"] == "birchwood_collision_intake_v1"
    assert redacted["decision_trace"][0]["disposition"] == "URGENT"
    assert (
        redacted["channel_metadata"]["scripted_intake"]["fields"]["caller_name"]
        == "[REDACTED]"
    )
    OrchestratorSession.model_validate(redacted)
