"""PR 5 — pilot metrics script: every success metric computable from data."""

from datetime import UTC, datetime, timedelta

from src.orchestrator.schemas import ConversationTurn, OrchestratorSession
from scripts.pilot_metrics import compute_costs, compute_metrics


def _session(
    session_id: str,
    *,
    outcome: str = "COMPLETED_INTAKE",
    flags: list[str] | None = None,
    human_review: bool = False,
    callback: bool = False,
    finalized: bool = True,
    duration_s: int = 120,
) -> OrchestratorSession:
    start = datetime.now(UTC) - timedelta(minutes=30)
    session = OrchestratorSession(session_id=session_id, call_sid=f"CA-{session_id}")
    session.vertical_key = "automotive_collision"
    session.workflow_id = "birchwood_collision_intake_v1"
    session.is_finalized = finalized
    session.created_at = start
    session.conversation = [
        ConversationTurn(role="assistant", text="Hello " * 30, timestamp=start),
        ConversationTurn(
            role="caller",
            text="story",
            timestamp=start + timedelta(seconds=duration_s // 2),
        ),
        ConversationTurn(
            role="assistant",
            text="Closing " * 20,
            timestamp=start + timedelta(seconds=duration_s),
        ),
    ]
    if finalized:
        session.channel_metadata["workflow_final_result"] = {
            "structured_output": {
                "intake_record": {
                    "recommended_routing": outcome,
                    "flags": flags or [],
                    "human_review_required": human_review,
                    "callback_needed": callback,
                }
            }
        }
    return session


def test_compute_metrics_counts_everything():
    sessions = [
        _session("clean", duration_s=100),
        _session(
            "injured",
            flags=["injuries_reported"],
            human_review=True,
            duration_s=200,
        ),
        _session(
            "callback",
            outcome="INCOMPLETE_CALLBACK_NEEDED",
            callback=True,
            duration_s=150,
        ),
        _session("dropped", finalized=False, duration_s=30),
    ]
    metrics = compute_metrics(sessions)
    assert metrics["total_calls"] == 4
    assert metrics["finalized_calls"] == 3
    # Only "clean" completed without any human rescue signal.
    assert metrics["completed_without_rescue_pct"] == 25.0
    assert metrics["drop_off_pct"] == 25.0
    assert metrics["callback_needed_pct"] == 25.0
    assert metrics["injury_flagged_count"] == 1
    assert metrics["injury_flagged_session_ids"] == ["injured"]
    assert metrics["avg_call_duration_seconds"] == 120.0
    assert metrics["outcomes"]["COMPLETED_INTAKE"] == 2
    assert metrics["avg_tts_characters"] > 0


def test_compute_costs_is_deterministic_no_llm():
    metrics = {"avg_call_duration_seconds": 120.0, "avg_tts_characters": 1_000}
    costs = compute_costs(
        metrics,
        twilio_per_minute=0.014,
        tts_per_million_chars=16.0,
        infra_per_day=3.0,
        calls_per_day=20,
    )
    assert costs["avg_call_minutes"] == 2.0
    assert costs["twilio_per_call_usd"] == 0.028
    assert costs["tts_per_call_usd"] == 0.016
    assert costs["variable_per_call_usd"] == 0.044
    assert costs["infra_per_call_usd"] == 0.15
    assert costs["total_per_call_usd"] == 0.194
    assert costs["assumptions"]["llm_per_call_usd"] == 0.0
