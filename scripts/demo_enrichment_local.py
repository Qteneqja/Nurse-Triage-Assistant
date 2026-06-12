"""Local end-to-end demo of the shadow-mode enrichment layer.

Seeds two finalized Birchwood demo calls (fake PII), runs the real
enrichment pipeline on them, and prints what was actually sent to the
provider (so you can verify redaction) plus each feature's output.

Provider selection:
  - If DEEPSEEK_API_KEY is set (env or .env), the REAL DeepSeek provider
    is used — this proves the live integration, costs a few cents.
  - Otherwise a mock provider with canned outputs is used, so the script
    always works and still demonstrates the full pipeline + dashboard.

Everything runs in-process against the memory backend — nothing touches
staging, Postgres, or the live call flow.

Usage (from the repo root, venv active):

    python -m scripts.demo_enrichment_local            # run + print report
    python -m scripts.demo_enrichment_local --serve    # then open
        http://127.0.0.1:8000/dashboard/enrichment
"""

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

# Demo posture must be set BEFORE src.config is imported.
os.environ["ENRICHMENT_ENABLED"] = "true"
os.environ["STORAGE_BACKEND"] = "memory"
os.environ["APP_ENV"] = "development"
os.environ["ENVIRONMENT"] = "development"
os.environ["DASHBOARD_ENABLED"] = "true"
os.environ.setdefault("ENRICHMENT_PII_MODE", "redact")

from src.enrichment import provider as provider_module  # noqa: E402
from src.enrichment.insights import compute_insights  # noqa: E402
from src.enrichment.pipeline import (  # noqa: E402
    build_context,
    run_enrichment_for_session,
)
from src.enrichment.provider import EnrichmentProvider  # noqa: E402
from src.orchestrator.schemas import (  # noqa: E402
    ConversationTurn,
    OrchestratorSession,
)
from src.storage.session_repository import get_session_repository  # noqa: E402

SEP = "-" * 72


class _DemoMockProvider:
    """Canned outputs so the demo works without an API key."""

    name = "mock-demo"
    model = "canned-1"

    async def call(self, messages, output_schema, max_tokens=800, temperature=0.2):
        from src.enrichment.features import (
            FollowupOutput,
            InjuryBranchQA,
            NormalizeOutput,
            QAOutput,
            RoutingOutput,
            SummaryOutput,
        )

        canned = {
            NormalizeOutput: NormalizeOutput(notes="(canned mock output)"),
            SummaryOutput: SummaryOutput(
                headline="(canned) Rear-end collision, drivable",
                summary="(canned) Set DEEPSEEK_API_KEY to see real output.",
                recommended_next_action="(canned) Advisor callback.",
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
                sms_draft="(canned) Hi [CUSTOMER_NAME], we received your details.",
                email_subject="(canned) Your Birchwood intake",
                email_draft="(canned) Hi [CUSTOMER_NAME], thanks for calling.",
            ),
            RoutingOutput: RoutingOutput(priority="normal", rationale="(canned)"),
        }
        return canned[output_schema].model_copy(deep=True)


def _seed_session(repo, session_id: str, *, injured: bool) -> OrchestratorSession:
    session = OrchestratorSession(session_id=session_id, call_sid=f"CA-{session_id}")
    session.vertical_key = "automotive_collision"
    session.workflow_id = "birchwood_collision_intake_v1"
    session.is_finalized = True
    session.created_at = datetime.now(UTC) - timedelta(minutes=30)
    start = session.created_at
    injury_line = (
        "My neck is a little sore from the seatbelt. "
        if injured
        else "Nobody was hurt, thankfully. "
    )
    turns = [
        ("assistant", "Thank you for calling Birchwood Automotive Group."),
        (
            "caller",
            "Hi, this is Dana Example. I got rear-ended at Pembina and "
            "Stafford about an hour ago. " + injury_line + "My number is "
            "204-555-0177 and my email is dana@example.com. It's a 2022 "
            "Honda Civic, rear bumper is cracked but it drives fine. MPI "
            "gave me claim number MPI-9921-07.",
        ),
        (
            "assistant",
            "Most importantly - call 9 1 1 if anyone is hurt. I've noted "
            "everything down and an advisor will call you back shortly.",
        ),
    ]
    session.conversation = [
        ConversationTurn(
            role=role, text=text, timestamp=start + timedelta(seconds=30 * i)
        )
        for i, (role, text) in enumerate(turns)
    ]
    session.channel_metadata["workflow_final_result"] = {
        "final_disposition": "COMPLETED_INTAKE",
        "structured_output": {
            "intake_record": {
                "caller_name": "Dana Example",
                "phone": "+12045550177",
                "email": "dana@example.com",
                "vehicle_year": 2022,
                "vehicle_make": "Honda",
                "vehicle_model": "Civic",
                "damage_type": "rear bumper",
                "is_drivable": True,
                "incident_description": (
                    "Rear-ended at Pembina and Stafford. Reach me at 204-555-0177."
                ),
                "incident_location": "Pembina at Stafford",
                "claim_number": "MPI-9921-07",
                "flags": ["injuries_reported"] if injured else [],
                "recommended_routing": "COMPLETED_INTAKE",
            }
        },
    }
    repo._backend.save_session(session)
    return session


def _print_report(repo, session: OrchestratorSession) -> None:
    record = session.channel_metadata["workflow_final_result"]["structured_output"][
        "intake_record"
    ]
    print(SEP)
    print(f"SESSION {session.session_id}  flags={record['flags'] or 'none'}")
    context = build_context(session)
    print(f"\nWhat the provider receives (pii_mode={context['pii_mode']}):")
    for line in context["transcript_text"].splitlines():
        print(f"  | {line}")
    for row in reversed(repo.get_enrichment_results(session.session_id)):
        payload = row["payload"]
        print(f"\n[{row['feature']}] status={row['status']}")
        if row["status"] != "completed":
            print(f"  {payload}")
        elif row["feature"] == "summary":
            print(f"  headline: {payload['headline']}")
            print(f"  summary:  {payload['summary']}")
            print(f"  next:     {payload['recommended_next_action']}")
        elif row["feature"] == "routing":
            print(f"  priority: {payload['priority']}")
            print(f"  location: {payload['suggested_location']}")
            print(f"  rationale: {payload['rationale']}")
        elif row["feature"] == "followup":
            print(f"  sms draft (as stored, tokens intact): {payload['sms_draft']}")
            print(f"  requires_human_approval: {payload['requires_human_approval']}")
        elif row["feature"] == "qa":
            print(f"  intake_completed: {payload['intake_completed']}")
            print(f"  sentiment: {payload['caller_sentiment']}")
            print(f"  injury_branch: {payload['injury_branch']}")
        elif row["feature"] == "normalize":
            print(f"  vehicle: {payload['vehicle']}")
            print(f"  damage:  {payload['damage']}")
            print(f"  needs review: {payload['fields_needing_review']}")


def main() -> int:
    serve = "--serve" in sys.argv

    if os.getenv("DEEPSEEK_API_KEY"):
        print("DEEPSEEK_API_KEY found -> using the REAL DeepSeek provider.")
        provider: EnrichmentProvider | None = None  # lazy default resolution
    else:
        print(
            "No DEEPSEEK_API_KEY -> using a canned mock provider. Set the "
            "key in .env to see real LLM output."
        )
        provider = _DemoMockProvider()
    if provider is not None:
        provider_module.set_enrichment_provider(provider)

    repo = get_session_repository()
    sessions = [
        _seed_session(repo, "demo-clean", injured=False),
        _seed_session(repo, "demo-injury", injured=True),
    ]

    async def _run() -> None:
        for session in sessions:
            await run_enrichment_for_session(session.session_id)

    asyncio.run(_run())

    for session in sessions:
        _print_report(repo, session)

    print(SEP)
    insights = compute_insights(sessions)
    print(
        f"INSIGHTS: status={insights['status']} sample={insights['sample_size']} "
        f"(needs {insights['min_calls_for_reliability']}+ to be reliable)"
    )
    print(SEP)

    if serve:
        import uvicorn

        from src.main import app

        print(
            "Dashboard: http://127.0.0.1:8000/dashboard/enrichment "
            "(dev mode - no token needed). Ctrl+C to stop."
        )
        uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
