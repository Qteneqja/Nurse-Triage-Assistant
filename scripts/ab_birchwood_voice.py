"""A/B voice harness for the Birchwood "Aurora" naturalness pass (Track A).

Renders one or two FIXED sample calls through BOTH the pre-Aurora static copy
and the new Aurora copy (per-call phrasing variation + slot-echo + backchannels
+ the once-per-call hesitation + expressive SSML), writing PAIRED before/after
artifacts for the SAME scenario so a human can compare by ear — or by transcript
when Azure Speech credentials aren't configured.

    python -m scripts.ab_birchwood_voice                # default ./ab_birchwood_out
    python -m scripts.ab_birchwood_voice --out DIR
    python -m scripts.ab_birchwood_voice --no-audio     # transcripts only

Sibling of scripts/measure_voice_latency.py — this does NOT modify it, so the
latency measurement keeps working unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import src.config as config
from src.orchestrator.schemas import OrchestratorSession
from src.verticals.automotive_collision import voice_naturalness as vn
from src.verticals.automotive_collision.constants import (
    BIRCHWOOD_COLLISION_WORKFLOW_ID,
)
from src.verticals.automotive_collision.prompts import (
    BIRCHWOOD_COLLISION_INTRO,
    BIRCHWOOD_COLLISION_PROMPTS,
)
from src.verticals.automotive_collision.workflow import (
    BirchwoodCollisionIntakeWorkflow,
)

# The pre-Aurora intro, kept here as the A/B baseline only.
OLD_INTRO = (
    "Thank you for calling Birchwood Automotive Group. This call may be "
    "recorded for training and quality purposes. I'm here to get your "
    "vehicle booked in and taken care of after your accident - we'll get "
    "you back on the road. And if you'd rather speak with one of our team "
    "right away, just say transfer or press 0."
)

SCENARIOS: list[dict] = [
    {
        "id": "honda_civic_drivable",
        "answers": {
            "caller_name": "Jane Doe",
            "phone": "204 555 0123",
            "vehicle_year": 2019,
            "vehicle_make": "Honda",
            "vehicle_model": "Civic",
            "damage_type": "rear bumper damage",
            "is_drivable": "yes",
            "filing_insurance_claim": "yes",
            "claim_number": "MPI 123456",
        },
    },
    {
        "id": "ford_f150_tow",
        "answers": {
            "caller_name": "Sam Carter",
            "phone": "431 555 0188",
            "vehicle_year": 2022,
            "vehicle_make": "Ford",
            "vehicle_model": "F-150",
            "damage_type": "front end, needs a tow",
            "is_drivable": "no",
            "filing_insurance_claim": "no",
            "claim_number": "none",
        },
    },
]


def _birchwood_session() -> OrchestratorSession:
    session = OrchestratorSession(session_id="ab", call_sid="ab")
    session.workflow_id = BIRCHWOOD_COLLISION_WORKFLOW_ID
    session.channel_metadata["scripted_intake"] = {"fields": {}}
    return session


def build_scenario_pair(scenario: dict) -> tuple[list[str], list[str]]:
    """Return (old_lines, new_lines) — the caller-heard lines for each version.

    Both start with the intro; then one line per scripted stage. The new lines
    fold in the backchannel for the previous answer, slot-echo, the per-call
    phrasing variant, and the single per-call hesitation.
    """
    intake = BirchwoodCollisionIntakeWorkflow().get_scripted_intake_definition()
    answers = scenario["answers"]

    old_lines = [OLD_INTRO]
    new_lines = [BIRCHWOOD_COLLISION_INTRO]

    session = _birchwood_session()
    fields = session.channel_metadata["scripted_intake"]["fields"]
    prev_stage = None
    for stage in intake.stages:
        field = stage.field_name
        if field not in answers:
            continue

        # OLD: the static prompt, no backchannel.
        old_lines.append(BIRCHWOOD_COLLISION_PROMPTS.get(field, stage.prompt))

        # NEW: backchannel (prev) + composed prompt (slot-echo + variant + once-
        # per-call hesitation).
        ack = vn.backchannel(session, prev_stage) if prev_stage is not None else None
        composed = vn.compose_stage_prompt(session, stage, dict(fields)) or stage.prompt
        new_lines.append(f"{ack} {composed}" if ack else composed)

        fields[field] = answers[field]
        prev_stage = stage

    return old_lines, new_lines


def _write_transcripts(out_dir: Path, scenario_id: str, old_lines, new_lines) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "transcript_old.txt").write_text(
        "\n\n".join(old_lines), encoding="utf-8"
    )
    (out_dir / "transcript_new.txt").write_text(
        "\n\n".join(new_lines), encoding="utf-8"
    )


async def _maybe_render(out_dir: Path, side: str, lines, *, expressive: bool) -> int:
    """Render each line to <side>_NN.mp3; return how many were written."""
    from src.utils.azure_tts import synthesize_speech

    voice = getattr(
        config, "BIRCHWOOD_AZURE_TTS_VOICE", "en-US-Bree:DragonHDLatestNeural"
    )
    rate = getattr(config, "BIRCHWOOD_AZURE_TTS_RATE", "+3%")
    pitch = getattr(config, "BIRCHWOOD_AZURE_TTS_PITCH", "+0%")
    break_ms = int(getattr(config, "BIRCHWOOD_AZURE_TTS_BREAK_MS", 250))
    written = 0
    for index, line in enumerate(lines):
        audio = await synthesize_speech(
            line,
            voice,
            rate=rate,
            pitch=pitch,
            break_ms=break_ms,
            expressive=expressive,
        )
        if not audio:
            continue
        (out_dir / f"{side}_{index:02d}.mp3").write_bytes(audio)
        written += 1
    return written


async def main() -> int:
    parser = argparse.ArgumentParser(description="Birchwood Aurora A/B voice harness")
    parser.add_argument("--out", default="./ab_birchwood_out")
    parser.add_argument("--no-audio", action="store_true")
    args = parser.parse_args()

    base = Path(args.out)
    has_key = bool(getattr(config, "AZURE_SPEECH_KEY", ""))
    render_audio = not args.no_audio and has_key

    for scenario in SCENARIOS:
        out_dir = base / scenario["id"]
        old_lines, new_lines = build_scenario_pair(scenario)
        _write_transcripts(out_dir, scenario["id"], old_lines, new_lines)
        print(f"[{scenario['id']}] wrote paired transcripts to {out_dir}")
        if render_audio:
            n_old = await _maybe_render(out_dir, "old", old_lines, expressive=False)
            n_new = await _maybe_render(out_dir, "new", new_lines, expressive=True)
            print(f"[{scenario['id']}] wrote {n_old} old + {n_new} new audio clips")

    if not render_audio:
        reason = "--no-audio" if args.no_audio else "AZURE_SPEECH_KEY not set"
        print(
            f"\nAudio not rendered ({reason}). Transcripts written under {base} — "
            "compare old vs new by reading, or re-run with Azure credentials to "
            "produce paired before/after clips to listen to."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
