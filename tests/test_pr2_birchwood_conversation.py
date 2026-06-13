"""PR 2 — Birchwood conversation experience.

Locks the narrative-first design: invite the full story, prefill every
field the story answered (deterministic extraction), gap-fill only missing
REQUIRED fields, confirm with a readback, close with next steps. Plus the
injury question branch (Invariant 3) and the correction flow.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import BackgroundTasks

from src.platform.workflows.schemas import ResolvedWorkflowRoute
from src.verticals.automotive_collision.constants import (
    AUTOMOTIVE_COLLISION_VERTICAL,
    BIRCHWOOD_COLLISION_WORKFLOW_ID,
)
from src.verticals.automotive_collision.narrative_extraction import (
    extract_from_narrative,
)
from src.verticals.automotive_collision.workflow import (
    BirchwoodCollisionIntakeWorkflow,
)

BIRCHWOOD_ROUTE = ResolvedWorkflowRoute(
    vertical_key=AUTOMOTIVE_COLLISION_VERTICAL,
    workflow_id=BIRCHWOOD_COLLISION_WORKFLOW_ID,
    workflow_version="v1",
)

RICH_STORY = (
    "I got rear-ended yesterday evening at Pembina and Stafford. It's my "
    "2021 Toyota Corolla - the rear bumper and trunk are smashed up but it "
    "still drives fine. The other driver and I exchanged information, the "
    "police came and took a report, and I took some photos of the damage. "
    "I'm going through MPI, the claim number is MPI-4452-21, and nobody "
    "was hurt."
)

_TTS_OFF = patch(
    "src.twilio.routes.text_to_speech_url",
    new=AsyncMock(return_value=None),
)


def _setup_memory_repo(mp: pytest.MonkeyPatch):
    from src.platform.workflows.router import reset_workflow_route_resolver
    from src.storage.factory import reset_storage_backend
    from src.storage.session_repository import (
        get_session_repository,
        reset_session_repository,
    )

    mp.setattr("src.config.STORAGE_BACKEND", "memory")
    mp.setattr("src.config.ENVIRONMENT", "development")
    mp.setattr("src.config.APP_ENV", "development")
    mp.setattr("src.config.DATABASE_URL", None)
    mp.setattr("src.config.ENABLE_SHARED_NUMBER_VERTICAL_MENU", False)
    reset_session_repository()
    reset_storage_backend()
    reset_workflow_route_resolver()
    return get_session_repository()


def _start_birchwood_call(repo, call_sid: str):
    workflow = BirchwoodCollisionIntakeWorkflow()
    intake = workflow.get_scripted_intake_definition()
    first = intake.stages[0]
    session = repo.create_session(call_sid=call_sid, workflow_route=BIRCHWOOD_ROUTE)
    session.channel_metadata["stage"] = first.stage_id
    session.channel_metadata["scripted_intake"] = {
        "workflow_id": BIRCHWOOD_COLLISION_WORKFLOW_ID,
        "current_index": 0,
        "current_stage_id": first.stage_id,
        "fields": {},
        "attempts": {},
        "completed": False,
    }
    repo.persist_session(session)
    return session


async def _gather(call_sid: str, speech: str | None, digits: str | None = None):
    from src.twilio import routes as twilio_routes

    return await twilio_routes.handle_gather(
        SimpleNamespace(headers={}),
        BackgroundTasks(),
        CallSid=call_sid,
        SpeechResult=speech,
        Digits=digits,
    )


async def _finalize_via_thinking(call_sid: str):
    from src.twilio import routes as twilio_routes

    task, _ = twilio_routes._pending_turns[call_sid]
    await task
    return await twilio_routes.handle_thinking(
        SimpleNamespace(headers={}),
        BackgroundTasks(),
        CallSid=call_sid,
    )


def _final_record(repo, call_sid: str) -> dict:
    stored = repo.load_session_by_call(call_sid)
    return stored.channel_metadata["workflow_final_result"]["structured_output"][
        "intake_record"
    ]


# ---------------------------------------------------------------------------
# Narrative extraction precision
# ---------------------------------------------------------------------------


def test_rich_story_extracts_most_fields():
    fields = extract_from_narrative(RICH_STORY).fields
    assert fields["is_drivable"] is True
    assert fields["vehicle_year"] == 2021
    assert fields["vehicle_make"] == "Toyota"
    assert fields["vehicle_model"] == "Corolla"
    assert "bumper" in fields["damage_type"]
    assert fields["incident_datetime"].lower().startswith("yesterday")
    assert "pembina and stafford" in fields["incident_location"].lower()
    assert fields["police_report_filed"] == "yes"
    assert fields["photos_available"] == "yes"
    assert fields["other_parties"] == "another vehicle involved"
    assert fields["filing_insurance_claim"] is True
    assert fields["insurance_provider"] == "MPI"
    assert fields["claim_number"].replace(" ", "-").startswith("MPI-4452")
    assert fields["injuries_state"] == "denied"


def test_extraction_is_conservative_on_ambiguity():
    fields = extract_from_narrative(
        "The other driver couldn't drive straight and hit me near the mall."
    ).fields
    # No vehicle facts were stated about MY car — nothing gets prefilled.
    assert "is_drivable" not in fields
    assert "vehicle_year" not in fields

    # A bare number is never a vehicle year.
    fields = extract_from_narrative("There were like 2015 people at the event.").fields
    assert "vehicle_year" not in fields


def test_extraction_handles_towed_and_private_pay():
    fields = extract_from_narrative(
        "I hit a deer on the highway, the truck won't start so it got towed. "
        "I'll just pay out of pocket."
    ).fields
    assert fields["is_drivable"] is False
    assert fields["filing_insurance_claim"] is False
    assert fields["insurance_provider"] == "private pay"
    assert fields["other_parties"] == "single vehicle"


def test_extraction_audit_carries_source_phrases():
    prefill = extract_from_narrative(RICH_STORY)
    audited_fields = {entry["field"] for entry in prefill.audit}
    assert "vehicle_make" in audited_fields
    assert all(entry["source"] for entry in prefill.audit)


# ---------------------------------------------------------------------------
# End-to-end: story → gap-fill → readback → confirm → close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rich_story_full_flow_asks_only_gap_fill_questions():
    call_sid = "CA-PR2-RICH"
    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        _start_birchwood_call(repo, call_sid)

        r = await _gather(call_sid, RICH_STORY)
        assert "go on, i'm listening" in r.body.decode().lower()
        r = await _gather(call_sid, "that's everything")

        # Story answered injuries/drivability/damage/vehicle/when/where/
        # insurance — the first gap-fill question is the rebuilt check.
        body = r.body.decode().lower()
        assert "rebuilt or salvage" in body

        r = await _gather(call_sid, "no never")
        assert "full name" in r.body.decode().lower()
        r = await _gather(call_sid, "Pat Tester")
        assert "phone number" in r.body.decode().lower()
        r = await _gather(call_sid, "204 555 0101")

        # Confirmation readback, built dynamically from captured fields.
        body = r.body.decode()
        assert "did i get all of that right" in body.lower()
        assert "2021 Toyota Corolla" in body
        assert "Pat Tester" in body

        r = await _gather(call_sid, "yes that's right")
        final = await _finalize_via_thinking(call_sid)
        closing = final.body.decode()
        assert "what happens next" in closing.lower()
        assert "doesn't confirm coverage" in closing.lower()

        record = _final_record(repo, call_sid)
        assert record["recommended_routing"] == "COMPLETED_INTAKE"
        assert record["injuries_state"] == "denied"
        assert "injuries_denied" in record["flags"]
        assert record["confirmation_ack"] == "yes"
        assert record["correction_note"] is None
        assert record["police_report_filed"] == "yes"
        assert record["insurance_provider"] == "MPI"
        assert record["plain_summary"]
        assert "SITUATION:" in record["shop_summary"]
        assert "RECOMMENDED ACTION:" in record["shop_summary"]


@pytest.mark.asyncio
async def test_sparse_story_asks_injury_check_first():
    call_sid = "CA-PR2-SPARSE"
    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        _start_birchwood_call(repo, call_sid)

        await _gather(call_sid, "Someone bumped into my car.")
        r = await _gather(call_sid, "that's everything")
        assert "was anyone hurt" in r.body.decode().lower()

        r = await _gather(call_sid, "no, everyone is fine")
        # Denied — no advisory, straight to the drivability gap-fill.
        body = r.body.decode().lower()
        assert "9 1 1" not in body
        assert "does it need a tow" in body

        stored = repo.load_session_by_call(call_sid)
        fields = stored.channel_metadata["scripted_intake"]["fields"]
        assert fields["injuries_state"] == "denied"


@pytest.mark.asyncio
async def test_plain_yes_to_injury_question_triggers_advisory_and_flag():
    call_sid = "CA-PR2-INJURY-YES"
    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        _start_birchwood_call(repo, call_sid)

        await _gather(call_sid, "Got rear ended at a light.")
        r = await _gather(call_sid, "that's it")
        assert "was anyone hurt" in r.body.decode().lower()

        # A plain "yes" has no injury keywords — the advisory must key off
        # the recorded state (Invariant 3).
        r = await _gather(call_sid, "yes")
        body = r.body.decode()
        assert "9 1 1" in body

        stored = repo.load_session_by_call(call_sid)
        fields = stored.channel_metadata["scripted_intake"]["fields"]
        assert fields["injuries_state"] == "reported"


@pytest.mark.asyncio
async def test_readback_correction_flow_flags_human_review():
    call_sid = "CA-PR2-CORRECTION"
    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        _start_birchwood_call(repo, call_sid)

        await _gather(call_sid, RICH_STORY)
        await _gather(call_sid, "that's everything")
        await _gather(call_sid, "no never")  # rebuilt
        await _gather(call_sid, "Pat Tester")  # name
        r = await _gather(call_sid, "204 555 0101")  # phone -> readback
        assert "did i get all of that right" in r.body.decode().lower()

        r = await _gather(call_sid, "no, that's not right")
        assert "what should i correct" in r.body.decode().lower()

        await _gather(call_sid, "the phone number should end in nine nine")
        final = await _finalize_via_thinking(call_sid)
        assert final.status_code == 200

        record = _final_record(repo, call_sid)
        assert record["confirmation_ack"] == "no"
        assert "nine nine" in record["correction_note"]
        assert "readback_correction" in record["flags"]
        assert record["human_review_required"] is True


@pytest.mark.asyncio
async def test_private_pay_skips_claim_question():
    call_sid = "CA-PR2-PRIVATE"
    with pytest.MonkeyPatch.context() as mp, _TTS_OFF:
        repo = _setup_memory_repo(mp)
        session = _start_birchwood_call(repo, call_sid)
        # Jump straight to the insurance question with everything else done.
        workflow = BirchwoodCollisionIntakeWorkflow()
        intake = workflow.get_scripted_intake_definition()
        filing = next(
            s for s in intake.stages if s.field_name == "filing_insurance_claim"
        )
        session.channel_metadata["stage"] = filing.stage_id
        session.channel_metadata["scripted_intake"].update(
            {
                "current_index": intake.stages.index(filing),
                "current_stage_id": filing.stage_id,
                "fields": {
                    "incident_description": "Hit a pole. Nobody was hurt.",
                    "injuries_state": "denied",
                    "is_drivable": True,
                    "damage_type": "front bumper",
                    "vehicle_year": 2020,
                    "vehicle_make": "Toyota",
                    "vehicle_model": "Camry",
                    "rebuilt_salvage_status": "no",
                    "incident_datetime": "yesterday",
                    "incident_location": "parking lot",
                },
            }
        )
        repo.persist_session(session)

        r = await _gather(call_sid, "I'll be paying privately, out of pocket")
        # Claim-number question skipped — straight to contact details.
        body = r.body.decode().lower()
        assert "claim number" not in body
        assert "full name" in body

        stored = repo.load_session_by_call(call_sid)
        fields = stored.channel_metadata["scripted_intake"]["fields"]
        assert fields["insurance_provider"] == "private pay"
