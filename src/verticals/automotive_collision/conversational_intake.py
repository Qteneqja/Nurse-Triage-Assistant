"""Gated-LLM conversational intake for the Birchwood collision vertical (premium tier).

When ``BIRCHWOOD_CONVERSATIONAL_INTAKE`` is on, the Birchwood workflow runs this
fluid conversation instead of the deterministic scripted Q&A. An LLM conducts a
natural, out-of-order conversation and extracts the fields a collision specialist
needs; the DETERMINISTIC rules still decide the disposition, and every
caller-facing string still passes the safety gate.

Safety design (consistent with ORCA's invariants):

* **The LLM never makes the disposition.** ``classify_collision_intake``
  (deterministic) does, at finalize — exactly as the scripted flow. The LLM only
  converses and extracts intake fields.
* **Every LLM utterance is gated.** ``response_to_caller`` is one of the gate's
  ``_OUTBOUND_TEXT_FIELDS``, so ``GuardedLLM.structured_call`` runs it through
  ``gate_outbound_text`` automatically (``kind="question"``). We pass
  ``store_phi=True`` so the caller's OWN contact details aren't masked in the
  confirmation echo (this is non-clinical collision intake, and the
  safety-critical checks — role-claim, diagnosis, unsafe-instruction, and the
  PHI-probing block for address/email/SSN/policy-number — still run).
* **The spontaneous-injury reflex** is the platform overlay (engine level) and
  fires on every dynamic turn regardless of this module.
* On any LLM failure the caller (the workflow) **fails closed to a callback**.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field, field_validator

from src.orchestrator.schemas import OrchestratorSession
from src.safety.gate import GateContext

# Required fields a collision specialist needs before the call can finish.
REQUIRED_FIELDS: tuple[str, ...] = (
    "caller_name",
    "phone",
    "vehicle_year",
    "vehicle_make",
    "vehicle_model",
    "damage_type",
    "is_drivable",
)
_OPTIONAL_FIELDS: tuple[str, ...] = ("filing_insurance_claim", "claim_number")

# Hard cap so a stuck/looping call always hands off to a human callback.
MAX_TURNS = 14

# Consecutive turn failures tolerated before handing off. Below this we recover
# in-call ("sorry, could you say that again?") instead of ending the call — so a
# weird utterance or a transient model hiccup never drops the caller mid-call.
MAX_ERRORS = 3


class BirchwoodTurnOutput(BaseModel):
    """One conversational turn: what to say next + anything learned this turn."""

    # NOTE: ``response_to_caller`` is deliberately named — it is one of the gate's
    # _OUTBOUND_TEXT_FIELDS, so structured_call auto-gates it before it is spoken.
    response_to_caller: str = Field(
        description="What Aurora says next — 1 to 3 short, natural sentences."
    )
    caller_name: str | None = None
    phone: str | None = None
    vehicle_year: str | None = None
    vehicle_make: str | None = None
    vehicle_model: str | None = None
    damage_type: str | None = None
    is_drivable: str | None = Field(
        default=None, description='"yes", "no", or "unsure"'
    )
    filing_insurance_claim: str | None = Field(
        default=None, description='"yes", "no", or "unsure"'
    )
    claim_number: str | None = None
    caller_wants_human: bool = Field(
        default=False,
        description="true if the caller asks for a person / transfer / press 0",
    )
    ready_to_finalize: bool = Field(
        default=False,
        description="true ONLY when every required field is captured and confirmed",
    )

    @field_validator(
        "caller_name",
        "phone",
        "vehicle_year",
        "vehicle_make",
        "vehicle_model",
        "damage_type",
        "is_drivable",
        "filing_insurance_claim",
        "claim_number",
        mode="before",
    )
    @classmethod
    def _coerce_to_str(cls, value):
        """Coerce LLM-returned values to strings.

        LLMs routinely return a year/phone/claim number as a JSON *number* and a
        yes/no field as a JSON *boolean*. Without this, Pydantic would reject the
        turn (validation error) and the caller would hear the recovery prompt —
        the "couldn't register my callback number" failure. Booleans map to
        yes/no (correct for the drivable / insurance fields); everything else
        becomes its string form.
        """
        if value is None:
            return None
        if isinstance(value, bool):
            return "yes" if value else "no"
        return str(value)


SYSTEM_PROMPT = """You are "Aurora", the automated voice assistant for Birchwood Automotive Group's collision intake line. You already told the caller you're an automated assistant. Speak warmly and naturally, like a helpful Birchwood service advisor on the phone. The caller may have just been in a collision — lead with brief reassurance.

YOUR JOB: have a natural, flowing conversation to collect the details a collision specialist needs, then hand off. Capture whatever the caller volunteers, in any order — do NOT interrogate them one rigid question at a time. Acknowledge what they say, answer brief questions, and ask only for what's still missing.

REQUIRED details to collect before finishing:
- caller_name (full name)
- phone (best callback number)
- vehicle_year, vehicle_make, vehicle_model
- damage_type (a short description of the damage)
- is_drivable ("yes" if they can drive it in, "no" if it needs a tow, "unsure")
Nice to have: whether they've opened an MPI claim (filing_insurance_claim "yes"/"no") and the MPI claim_number if they have it.

RULES (important):
- Only collect the fields above. NEVER ask for a home address, an email, an SSN, credit-card or bank details, or an insurance "policy number". If you need the claim reference, ask for the "MPI claim number".
- You are an automotive intake assistant, NOT a medical professional. Never claim to be a nurse, doctor, or clinician, and never say you can diagnose, prescribe, or treat anything.
- Do NOT promise repair costs, pricing, timelines, coverage, or appointments — a Birchwood advisor confirms all of that on the callback. If asked, say the advisor will go over those details.
- If the caller mentions anyone was hurt or injured, gently tell them to call 9 1 1 or get medical help if needed, then continue.
- If the caller asks to talk to a person / be transferred / press 0, set caller_wants_human=true.
- Keep each spoken turn short and conversational (1 to 3 sentences). Ask one thing at a time unless they volunteer more.

OUTPUT: Return JSON for the schema. Put what you want to SAY next in "response_to_caller". Fill any fields you learned (leave unknown ones null). Set ready_to_finalize=true ONLY when every REQUIRED field is captured and you've briefly confirmed the key details."""


def missing_required_fields(fields: dict) -> list[str]:
    """Required field keys not yet captured (empty/whitespace counts as missing)."""
    return [name for name in REQUIRED_FIELDS if not str(fields.get(name) or "").strip()]


def _captured(fields: dict) -> dict:
    return {
        name: fields.get(name)
        for name in (*REQUIRED_FIELDS, *_OPTIONAL_FIELDS)
        if str(fields.get(name) or "").strip()
    }


def build_messages(session: OrchestratorSession, fields: dict) -> list[dict]:
    """System prompt (with captured-state) + the conversation so far.

    ``session.conversation`` already includes the caller's latest utterance
    (the workflow appends it before calling), so it is the final user message.
    """
    captured = _captured(fields)
    missing = missing_required_fields(fields)
    state = (
        f"\n\nDetails captured so far: {json.dumps(captured) if captured else 'none yet'}."
        f"\nStill missing (required): "
        f"{', '.join(missing) if missing else 'none — confirm the key details and finish'}."
        "\nDo not re-ask for details already captured."
    )
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT + state}]
    for turn in session.conversation:
        if not turn.text:
            continue
        role = "user" if turn.role == "caller" else "assistant"
        messages.append({"role": role, "content": turn.text})
    return messages


async def run_turn(
    guarded,
    session: OrchestratorSession,
    user_text: str,
    *,
    store_phi: bool = True,
) -> BirchwoodTurnOutput:
    """Run one gated LLM turn. Raises ``LLMCallError`` on failure (caller fails closed).

    ``response_to_caller`` is auto-gated by ``structured_call`` (kind="question").
    """
    fields = (session.channel_metadata.get("scripted_intake") or {}).get("fields") or {}
    messages = build_messages(session, fields)
    ctx = GateContext(
        session_id=session.session_id,
        caller_utterance=user_text,
        # Non-clinical collision intake: don't mask the caller's own name/phone in
        # the spoken confirmation. The role-claim / diagnosis / unsafe-instruction
        # / PHI-probing checks all still run.
        store_phi=store_phi,
    )
    return await guarded.structured_call(
        messages=messages,
        output_schema=BirchwoodTurnOutput,
        ctx=ctx,
        kind="question",
        max_tokens=400,
        temperature=0.4,
    )


def merge_extracted_fields(
    session: OrchestratorSession, output: BirchwoodTurnOutput
) -> None:
    """Write non-empty extracted fields into the scripted_intake fields dict.

    Uses the SAME field keys the scripted flow uses, so the existing deterministic
    finalize (``_intake_from_session`` -> ``classify_collision_intake``) consumes
    them unchanged. The make is canonicalised exactly as the scripted path does.
    """
    from src.verticals.automotive_collision.workflow import _normalize_make

    scripted = session.channel_metadata.setdefault("scripted_intake", {})
    fields = scripted.setdefault("fields", {})
    make = _normalize_make(output.vehicle_make) if output.vehicle_make else None
    mapping = {
        "caller_name": output.caller_name,
        "phone": output.phone,
        "vehicle_year": output.vehicle_year,
        "vehicle_make": make,
        "vehicle_model": output.vehicle_model,
        "damage_type": output.damage_type,
        "is_drivable": output.is_drivable,
        "filing_insurance_claim": output.filing_insurance_claim,
        "claim_number": output.claim_number,
    }
    for key, value in mapping.items():
        cleaned = value.strip() if isinstance(value, str) else value
        if cleaned not in (None, ""):
            fields[key] = cleaned


def is_transfer_request(text: str) -> bool:
    """Deterministic 'get me a human' detector (belt-and-suspenders to the LLM flag)."""
    normalized = (text or "").strip().lower()
    if normalized in {"0", "zero"}:
        return True
    return any(
        phrase in normalized
        for phrase in (
            "transfer",
            "speak with someone",
            "speak to someone",
            "talk to someone",
            "talk to a person",
            "real person",
            "human please",
            "person please",
            "representative",
            "operator",
            "press zero",
        )
    )
