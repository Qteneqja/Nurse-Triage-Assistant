"""
Voice-pipeline latency harness — gather vs ConversationRelay.

WHAT THIS MEASURES (offline, here): the APP-SIDE per-turn processing latency —
the time the server spends turning one caller utterance into the next response,
EXCLUDING the LLM round-trip (stubbed) and EXCLUDING all Twilio/STT/TTS network
time. Both transports call the same orchestrator, so this isolates the transport
glue overhead (scripted-intake handling + WS send vs TwiML build).

WHAT THIS CANNOT MEASURE (and must NOT be reported as the gate): the TRUE
end-to-end metric the migration is justified on — "caller stops speaking ->
agent audio starts" (p50/p95). That depends on streaming STT finalization
(Deepgram), TTS time-to-first-audio, and — for gather — the /thinking poll-loop
redirects + Azure-TTS-synth-then-<Play> round-trip. Those are Twilio/provider
side and require a LIVE STAGED CALL. See "Staging methodology" below.

Usage:
    python -m scripts.measure_voice_latency --runs 30

This prints app-side p50/p95 for the ConversationRelay turn path and the staging
methodology for the real end-to-end gate. It deliberately does NOT print a
gather-vs-CR end-to-end comparison, because that number cannot be produced
honestly offline.
"""

from __future__ import annotations

import argparse
import os
import statistics
import time

# Force an offline, deterministic environment before importing the app.
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("USE_MOCK_LLM", "true")
os.environ.setdefault("STORAGE_BACKEND", "memory")
os.environ.setdefault("VOICE_OUTPUT_MODE", "cr_native")  # avoid Azure TTS network


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[k]


def measure_cr_appside(runs: int) -> list[float]:
    """Drive a full CR call (scripted intake + 1 dynamic turn) with a stub engine
    and record the app-side latency of each caller-turn -> response cycle."""
    from fastapi.testclient import TestClient

    from src.platform.workflows.schemas import WorkflowTurnResult
    from src.twilio import conversation_relay as cr

    class _StubEngine:
        async def handle_turn(self, context, workflow_input):
            return WorkflowTurnResult(
                assistant_text="Where exactly is the pain?",
                stage="DYNAMIC",
                should_continue=True,
                should_finalize=False,
                escalation_required=False,
                updated_state=workflow_input.session_state,
            )

    cr.get_workflow_engine = lambda: _StubEngine()  # type: ignore[assignment]

    from src.main import app

    per_turn: list[float] = []
    with TestClient(app) as client:
        for i in range(runs):
            with client.websocket_connect("/api/v1/voice/relay") as ws:
                ws.send_json(
                    {"type": "setup", "callSid": f"LAT_{i}", "to": "+15555550100"}
                )
                ws.receive_json()  # greeting
                ws.receive_json()  # NAME prompt
                for ans in ("John Smith", "45", "male", "my chest hurts"):
                    t0 = time.perf_counter()
                    ws.send_json({"type": "prompt", "voicePrompt": ans, "last": True})
                    ws.receive_json()  # next prompt / dynamic response
                    per_turn.append((time.perf_counter() - t0) * 1000.0)
    return per_turn


STAGING_METHODOLOGY = """
STAGING METHODOLOGY (the real end-to-end gate — run this before flipping the default)
------------------------------------------------------------------------------------
1. Deploy this branch to staging with VOICE_PIPELINE set per arm:
     arm A: VOICE_PIPELINE=gather
     arm B: VOICE_PIPELINE=conversation_relay  VOICE_OUTPUT_MODE=azure_play
     arm C: VOICE_PIPELINE=conversation_relay  VOICE_OUTPUT_MODE=cr_native
2. Place >= 20 scripted test calls per arm (use the Birchwood/healthcare call
   checklist), speaking the same scripted answers each time.
3. Measure "caller stops speaking -> agent audio starts" per turn from the call
   recording / Twilio Voice Insights (or a stopwatch on playback). Compute p50/p95.
4. ACCEPT only if CR (arm B and/or C) p50 AND p95 are NO WORSE than gather (arm A);
   a meaningful improvement is expected from removing the /thinking poll-loop and
   Azure-synth round-trip, and from streaming STT/TTS. If CR is not better, report
   that honestly and do NOT claim the migration succeeded (do not flip the default).
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=30)
    args = parser.parse_args()

    samples = measure_cr_appside(args.runs)
    print("\n=== App-side per-turn latency (ConversationRelay, LLM stubbed) ===")
    print(f"samples: {len(samples)}")
    print(f"p50: {_percentile(samples, 50):.2f} ms")
    print(f"p95: {_percentile(samples, 95):.2f} ms")
    print(f"mean: {statistics.fmean(samples):.2f} ms" if samples else "mean: n/a")
    print(
        "\nNOTE: app-side overhead only. Both transports share the orchestrator, so "
        "this is NOT the migration's latency justification. The true end-to-end "
        "gate must be measured on a staged call:"
    )
    print(STAGING_METHODOLOGY)


if __name__ == "__main__":
    main()
