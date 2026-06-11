"""Shadow-mode enrichment layer — mock-provider tests (no live tokens).

Locks the non-negotiables:
  - master flag off → zero new paths, zero provider calls, zero writes
  - PII redaction verified on the actual payload sent to the provider
  - per-feature success / malformed-output / provider-failure paths,
    with failure isolation (one feature failing never blocks the rest)
  - deterministic overrides the LLM cannot undo (injury priority,
    human-approval on drafts, QA mismatch flag)
  - insights minimum-volume guard
  - the source call record is NEVER modified by enrichment
  - admin view gated by auth + master flag, preview-labeled
"""

import copy
from datetime import UTC, datetime, timedelta
from typing import Any, Type

import pytest
from fastapi.testclient import TestClient

from src.enrichment import provider as provider_module
from src.enrichment.features import (
    FollowupOutput,
    InjuryBranchQA,
    NormalizeOutput,
    QAOutput,
    RoutingOutput,
    SummaryOutput,
)
from src.enrichment.pipeline import run_enrichment_for_session
from src.enrichment.provider import (
    EnrichmentOutputError,
    EnrichmentProviderError,
)
from src.enrichment.redaction import redact_for_provider, restore_tokens
from src.main import app
from src.orchestrator.schemas import ConversationTurn, OrchestratorSession

# ---------------------------------------------------------------------------
# Mock provider
# ---------------------------------------------------------------------------

_VALID_OUTPUTS: dict[type, Any] = {
    NormalizeOutput: NormalizeOutput(
        fields_needing_review=["incident_datetime"], notes="cleaned"
    ),
    SummaryOutput: SummaryOutput(
        headline="Rear-end collision, drivable",
        summary="Customer was rear-ended; vehicle drivable.",
        recommended_next_action="Call back to schedule.",
    ),
    QAOutput: QAOutput(
        intake_completed=True,
        injury_branch=InjuryBranchQA(
            injury_mentioned_in_transcript=True,
            advisory_present_in_transcript=True,
            handled_correctly=True,
        ),
    ),
    FollowupOutput: FollowupOutput(
        sms_draft="Hi [CUSTOMER_NAME], we got your details.",
        email_subject="Your Birchwood intake",
        email_draft="Hi [CUSTOMER_NAME], thanks for calling.",
        requires_human_approval=False,  # the LLM trying to skip approval
    ),
    RoutingOutput: RoutingOutput(priority="low", rationale="looks minor"),
}


class MockProvider:
    name = "mock"
    model = "mock-1"

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.mode_by_schema: dict[type, str] = {}
        self.output_by_schema: dict[type, Any] = {}

    async def call(
        self,
        messages: list[dict],
        output_schema: Type[Any],
        max_tokens: int = 800,
        temperature: float = 0.2,
    ):
        self.calls.append({"messages": messages, "schema": output_schema.__name__})
        mode = self.mode_by_schema.get(output_schema, "ok")
        if mode == "malformed":
            raise EnrichmentOutputError("validation failed after repair")
        if mode == "fail":
            raise EnrichmentProviderError("provider timeout")
        if output_schema in self.output_by_schema:
            return self.output_by_schema[output_schema]
        if output_schema in _VALID_OUTPUTS:
            return _VALID_OUTPUTS[output_schema].model_copy(deep=True)
        # Insights commentary or other schemas: build minimal valid object.
        return output_schema.model_validate(
            {"observations": ["volume is healthy"], "caveats": ["small sample"]}
        )

    def all_prompt_text(self) -> str:
        return " ".join(m["content"] for call in self.calls for m in call["messages"])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _setup(mp: pytest.MonkeyPatch, *, enabled: bool = True, pii: str = "redact"):
    from src.storage.factory import reset_storage_backend
    from src.storage.session_repository import (
        get_session_repository,
        reset_session_repository,
    )

    mp.setattr("src.config.STORAGE_BACKEND", "memory")
    mp.setattr("src.config.APP_ENV", "development")
    mp.setattr("src.config.ENVIRONMENT", "development")
    mp.setattr("src.config.DATABASE_URL", None)
    mp.setattr("src.config.DASHBOARD_ENABLED", True)
    mp.setattr("src.config.ENRICHMENT_ENABLED", enabled)
    mp.setattr("src.config.ENRICHMENT_PII_MODE", pii)
    mp.setattr("src.config.DASHBOARD_RECORDS_SHOW_CONTACT", True)
    reset_session_repository()
    reset_storage_backend()
    mock = MockProvider()
    provider_module.set_enrichment_provider(mock)
    mp.setattr(provider_module, "_provider", mock, raising=False)
    return get_session_repository(), mock


@pytest.fixture(autouse=True)
def _reset_provider():
    yield
    provider_module.set_enrichment_provider(None)


def _bw_session(
    repo,
    session_id: str = "enr-1",
    *,
    flags: list[str] | None = None,
    finalized: bool = True,
) -> OrchestratorSession:
    session = OrchestratorSession(session_id=session_id, call_sid=f"CA-{session_id}")
    session.vertical_key = "automotive_collision"
    session.workflow_id = "birchwood_collision_intake_v1"
    session.is_finalized = finalized
    session.created_at = datetime.now(UTC) - timedelta(minutes=10)
    start = session.created_at
    session.conversation = [
        ConversationTurn(
            role="assistant", text="Thank you for calling.", timestamp=start
        ),
        ConversationTurn(
            role="caller",
            text=(
                "Hi, this is Jordan Sample. I got rear-ended on Pembina, my "
                "number is 204-555-0123 and my email is jordan@example.com. "
                "MPI gave me claim MPI-4452-21. My neck hurts a bit."
            ),
            timestamp=start + timedelta(seconds=60),
        ),
        ConversationTurn(
            role="assistant",
            text="Most importantly - call 9 1 1 if anyone is hurt.",
            timestamp=start + timedelta(seconds=90),
        ),
    ]
    session.channel_metadata["workflow_final_result"] = {
        "final_disposition": "COMPLETED_INTAKE",
        "structured_output": {
            "intake_record": {
                "caller_name": "Jordan Sample",
                "phone": "+12045550123",
                "email": "jordan@example.com",
                "vehicle_year": 2021,
                "vehicle_make": "Toyota",
                "vehicle_model": "Corolla",
                "damage_type": "rear bumper",
                "incident_description": (
                    "Rear-ended on Pembina. Reach me at 204-555-0123."
                ),
                "incident_location": "Pembina at Stafford",
                "claim_number": "MPI-4452-21",
                "flags": flags or [],
                "recommended_routing": "COMPLETED_INTAKE",
            }
        },
    }
    repo._backend.save_session(session)
    return session


def _rows(repo, session_id: str):
    return repo.get_enrichment_results(session_id)


# ---------------------------------------------------------------------------
# Master flag off — zero paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_master_flag_off_means_zero_calls_and_zero_writes():
    with pytest.MonkeyPatch.context() as mp:
        repo, mock = _setup(mp, enabled=False)
        _bw_session(repo)
        await run_enrichment_for_session("enr-1")
        assert mock.calls == []
        assert _rows(repo, "enr-1") == []


@pytest.mark.asyncio
async def test_trigger_helper_schedules_nothing_when_disabled():
    from fastapi import BackgroundTasks

    from src.twilio.routes import _maybe_schedule_enrichment

    with pytest.MonkeyPatch.context() as mp:
        repo, mock = _setup(mp, enabled=False)
        session = _bw_session(repo)
        tasks = BackgroundTasks()
        _maybe_schedule_enrichment(tasks, session)
        assert tasks.tasks == []


@pytest.mark.asyncio
async def test_healthcare_sessions_are_never_enriched():
    with pytest.MonkeyPatch.context() as mp:
        repo, mock = _setup(mp, enabled=True)
        session = OrchestratorSession(session_id="hc-1", call_sid="CA-hc")
        session.vertical_key = "healthcare"
        session.workflow_id = "healthcare_triage_v1"
        session.is_finalized = True
        repo._backend.save_session(session)
        await run_enrichment_for_session("hc-1")
        assert mock.calls == []
        assert _rows(repo, "hc-1") == []


# ---------------------------------------------------------------------------
# End-to-end with the mock provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_features_complete_and_source_record_untouched():
    with pytest.MonkeyPatch.context() as mp:
        repo, mock = _setup(mp)
        session = _bw_session(repo)
        before = copy.deepcopy(session.channel_metadata["workflow_final_result"])

        await run_enrichment_for_session("enr-1")

        rows = _rows(repo, "enr-1")
        assert {r["feature"] for r in rows} == {
            "normalize",
            "summary",
            "qa",
            "followup",
            "routing",
        }
        assert all(r["status"] == "completed" for r in rows)
        assert all(r["pii_mode"] == "redact" for r in rows)
        # Source record byte-identical after enrichment.
        reloaded = repo.load_session("enr-1")
        assert reloaded.channel_metadata["workflow_final_result"] == before


@pytest.mark.asyncio
async def test_redaction_no_pii_reaches_the_provider():
    with pytest.MonkeyPatch.context() as mp:
        repo, mock = _setup(mp, pii="redact")
        _bw_session(repo)
        await run_enrichment_for_session("enr-1")
        sent = mock.all_prompt_text()
        for identifier in (
            "Jordan Sample",
            "Jordan",
            "Sample",
            "204-555-0123",
            "2045550123",
            "+12045550123",
            "jordan@example.com",
            "MPI-4452-21",
        ):
            assert identifier not in sent, f"PII leaked to provider: {identifier}"
        assert "[CUSTOMER_NAME]" in sent
        assert "[PHONE_1]" in sent
        assert "[CLAIM_REF]" in sent
        # Working material is intentionally retained (documented decision).
        assert "Toyota" in sent
        assert "Pembina" in sent


@pytest.mark.asyncio
async def test_raw_mode_sends_raw_text():
    with pytest.MonkeyPatch.context() as mp:
        repo, mock = _setup(mp, pii="raw")
        _bw_session(repo)
        await run_enrichment_for_session("enr-1")
        sent = mock.all_prompt_text()
        assert "Jordan Sample" in sent
        assert "204-555-0123" in sent


# ---------------------------------------------------------------------------
# Failure paths — fail closed, isolated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_output_stored_as_needs_review_others_unaffected():
    with pytest.MonkeyPatch.context() as mp:
        repo, mock = _setup(mp)
        mock.mode_by_schema[NormalizeOutput] = "malformed"
        _bw_session(repo)
        await run_enrichment_for_session("enr-1")
        rows = {r["feature"]: r for r in _rows(repo, "enr-1")}
        assert rows["normalize"]["status"] == "needs_review"
        assert rows["normalize"]["payload"]["error"] == "malformed_output"
        assert rows["summary"]["status"] == "completed"
        assert rows["routing"]["status"] == "completed"


@pytest.mark.asyncio
async def test_provider_failure_stored_as_failed_record_intact():
    with pytest.MonkeyPatch.context() as mp:
        repo, mock = _setup(mp)
        for schema in (SummaryOutput, QAOutput):
            mock.mode_by_schema[schema] = "fail"
        session = _bw_session(repo)
        before = copy.deepcopy(session.channel_metadata["workflow_final_result"])
        await run_enrichment_for_session("enr-1")
        rows = {r["feature"]: r for r in _rows(repo, "enr-1")}
        assert rows["summary"]["status"] == "failed"
        assert rows["qa"]["status"] == "failed"
        assert rows["normalize"]["status"] == "completed"
        assert (
            repo.load_session("enr-1").channel_metadata["workflow_final_result"]
            == before
        )


@pytest.mark.asyncio
async def test_unknown_provider_fails_closed_silently():
    with pytest.MonkeyPatch.context() as mp:
        repo, _ = _setup(mp)
        provider_module.set_enrichment_provider(None)
        mp.setattr("src.config.LLM_PROVIDER", "not-a-real-provider")
        _bw_session(repo)
        await run_enrichment_for_session("enr-1")  # must not raise
        assert _rows(repo, "enr-1") == []


# ---------------------------------------------------------------------------
# Deterministic overrides
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_injury_flag_forces_urgent_priority():
    with pytest.MonkeyPatch.context() as mp:
        repo, mock = _setup(mp)
        _bw_session(repo, flags=["injuries_reported"])
        await run_enrichment_for_session("enr-1")
        routing = next(r for r in _rows(repo, "enr-1") if r["feature"] == "routing")
        assert routing["payload"]["priority"] == "urgent"
        assert "DETERMINISTIC OVERRIDE" in routing["payload"]["rationale"]


@pytest.mark.asyncio
async def test_followup_always_requires_human_approval():
    with pytest.MonkeyPatch.context() as mp:
        repo, mock = _setup(mp)
        _bw_session(repo)  # mock returns requires_human_approval=False
        await run_enrichment_for_session("enr-1")
        followup = next(r for r in _rows(repo, "enr-1") if r["feature"] == "followup")
        assert followup["payload"]["requires_human_approval"] is True


@pytest.mark.asyncio
async def test_qa_mismatch_when_llm_misses_flagged_injury():
    with pytest.MonkeyPatch.context() as mp:
        repo, mock = _setup(mp)
        mock.output_by_schema[QAOutput] = QAOutput(
            intake_completed=True,
            injury_branch=InjuryBranchQA(
                injury_mentioned_in_transcript=False,
                advisory_present_in_transcript=False,
                handled_correctly=True,
            ),
        )
        _bw_session(repo, flags=["injuries_reported"])
        await run_enrichment_for_session("enr-1")
        qa = next(r for r in _rows(repo, "enr-1") if r["feature"] == "qa")
        assert qa["payload"]["injury_branch"]["handled_correctly"] is False
        assert "MISMATCH" in qa["payload"]["injury_branch"]["notes"]


# ---------------------------------------------------------------------------
# Insights guard
# ---------------------------------------------------------------------------


def test_insights_insufficient_below_threshold():
    from src.enrichment.insights import compute_insights

    with pytest.MonkeyPatch.context() as mp:
        repo, _ = _setup(mp)
        mp.setattr("src.config.ENRICH_INSIGHTS_MIN_CALLS", 30)
        sessions = [_bw_session(repo, f"s-{i}") for i in range(3)]
        insights = compute_insights(sessions)
        assert insights["status"] == "insufficient_data"
        assert insights["reliable"] is False
        assert insights["sample_size"] == 3


def test_insights_reliable_above_threshold():
    from src.enrichment.insights import compute_insights

    with pytest.MonkeyPatch.context() as mp:
        repo, _ = _setup(mp)
        mp.setattr("src.config.ENRICH_INSIGHTS_MIN_CALLS", 2)
        sessions = [_bw_session(repo, f"s-{i}") for i in range(3)]
        insights = compute_insights(sessions)
        assert insights["status"] == "ok"
        assert insights["pct_with_claim_number"] == 100.0
        assert insights["drivable_vs_towed"]["unknown"] == 3


# ---------------------------------------------------------------------------
# Admin view
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


def test_admin_view_404_when_enrichment_disabled(client):
    with pytest.MonkeyPatch.context() as mp:
        _setup(mp, enabled=False)
        assert client.get("/api/v1/enrichment/recent").status_code == 404


def test_admin_view_requires_token_in_production(client):
    with pytest.MonkeyPatch.context() as mp:
        _setup(mp, enabled=True)
        mp.setattr("src.config.APP_ENV", "production")
        mp.setattr("src.config.ENVIRONMENT", "production")
        mp.setattr("src.config.DASHBOARD_ADMIN_TOKEN", "token-1234567890")
        assert client.get("/api/v1/enrichment/recent").status_code == 401
        ok = client.get(
            "/api/v1/enrichment/recent",
            headers={"X-Dashboard-Token": "token-1234567890"},
        )
        assert ok.status_code == 200


@pytest.mark.asyncio
async def test_admin_view_preview_labeled_and_tokens_restored(client):
    with pytest.MonkeyPatch.context() as mp:
        repo, mock = _setup(mp)
        _bw_session(repo)
        await run_enrichment_for_session("enr-1")

        recent = client.get("/api/v1/enrichment/recent").json()
        assert recent["preview"] is True
        assert "PREVIEW" in recent["label"]
        assert all("tokens_map" not in r for r in recent["results"])

        detail = client.get("/api/v1/enrichment/sessions/enr-1").json()
        followup = next(r for r in detail["results"] if r["feature"] == "followup")
        # Drafts stored with tokens; rendered locally for the admin view.
        assert "[CUSTOMER_NAME]" in followup["payload"]["sms_draft"]
        assert "Jordan Sample" in followup["rendered_drafts"]["sms_draft"]

        insights = client.get("/api/v1/enrichment/insights").json()
        assert insights["preview"] is True
        assert insights["insights"]["status"] == "insufficient_data"


# ---------------------------------------------------------------------------
# Redaction unit behavior + storage model
# ---------------------------------------------------------------------------


def test_redaction_catches_unknown_second_phone_via_sweep():
    record = {"caller_name": "Pat Doe", "phone": "+12045550100"}
    result = redact_for_provider(
        "Call Pat Doe at (204) 555-0100 or my work line 204.555.0199.",
        record,
    )
    assert "Pat Doe" not in result.text
    assert "555-0100" not in result.text and "555.0199" not in result.text
    assert "[PHONE_1]" in result.text and "[PHONE_2]" in result.text
    restored = restore_tokens(result.text, result.tokens)
    assert "Pat Doe" in restored


def test_enrichment_result_model_round_trip():
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from src.storage.models import Base, EnrichmentResultModel

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(
            EnrichmentResultModel(
                session_id="s1",
                call_sid="CA-1",
                feature="summary",
                status="completed",
                pii_mode="redact",
                provider="mock",
                payload_json={"headline": "x"},
                tokens_map_json={"[CUSTOMER_NAME]": "Pat"},
            )
        )
        db.commit()
        row = db.execute(select(EnrichmentResultModel)).scalar_one()
        assert row.feature == "summary"
        assert row.payload_json["headline"] == "x"
