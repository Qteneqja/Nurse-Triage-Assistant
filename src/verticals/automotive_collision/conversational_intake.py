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
import re

from pydantic import BaseModel, Field, field_validator

from src.orchestrator.schemas import OrchestratorSession
from src.safety.gate import GateContext

# Required data points the call must capture before it can finish (the AI's goal).
# Keys match the scripted-intake field names so _intake_from_fields consumes them
# unchanged (preferred_collision_center == the caller's preferred Birchwood location).
REQUIRED_FIELDS: tuple[str, ...] = (
    "caller_name",
    "phone",
    "email",
    "damage_type",
    "license_plate",
    "preferred_collision_center",
)
# The MPI claim number is required ONLY when the caller says a claim was filed
# (handled in missing_required_fields). Vehicle details + drivability are still
# captured opportunistically (useful for the advisor and BI) but no longer block.
_OPTIONAL_FIELDS: tuple[str, ...] = (
    "filing_insurance_claim",
    "claim_number",
    "vehicle_year",
    "vehicle_make",
    "vehicle_model",
    "is_drivable",
)

# Hard cap so a stuck/looping call always hands off to a human callback. The
# required set is larger now, so allow more turns before giving up.
MAX_TURNS = 18

# Latency: cap how much conversation history is replayed to the LLM each turn
# (the captured-state summary carries the rest). Keeps the prompt small and fast.
MAX_HISTORY_TURNS = 16

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
    email: str | None = None
    license_plate: str | None = None
    preferred_location: str | None = Field(
        default=None, description="the Birchwood location the caller prefers"
    )
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
        "email",
        "license_plate",
        "preferred_location",
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

YOUR JOB: have a natural, flowing conversation to collect the details below, then hand off. Capture whatever the caller volunteers, in any order — do NOT interrogate them one rigid question at a time. Acknowledge what they say, answer brief questions, and ask only for what's still missing.

REQUIRED details to collect before finishing (the call is not done until you have all of these):
- caller_name (the caller's full name)
- phone (best callback number)
- email (for the booking confirmation)
- damage_type (a clear description of the damage to the vehicle — get enough detail to picture it: where on the car, how bad, what happened)
- license_plate (the vehicle's licence plate)
- preferred_location (which Birchwood location is most convenient for them)
CONDITIONAL: ask whether they've opened an MPI claim (filing_insurance_claim "yes"/"no"); if YES, you must also collect the MPI claim_number.
Nice to have if it comes up naturally: vehicle_year, vehicle_make, vehicle_model, and whether it's drivable (is_drivable "yes"/"no"/"unsure").

SPELLING (important for accuracy):
- EMAIL: always ask the caller to SPELL OUT their email address ("could you spell that out for me, including what comes after the at sign?"), then READ IT BACK to confirm before moving on.
- NAME: if the name is unusual or you're not confident how it's spelled, ask the caller to spell their first and last name. Otherwise just confirm it back.

RULES (important):
- CAPTURE AS YOU GO: the moment the caller gives a detail (name, phone, email, the damage, licence plate, preferred location, claim info), you MUST copy it into the matching JSON field on that SAME turn. Do not merely acknowledge it in words and leave the field empty — an empty field means we have to ask again.
- Collect only the fields above. NEVER ask for a home address, an SSN, credit-card or bank details, or an insurance "policy number". If you need the claim reference, ask for the "MPI claim number" (email IS fine to collect).
- You are an automotive intake assistant, NOT a medical professional. Never claim to be a nurse, doctor, or clinician, and never say you can diagnose, prescribe, or treat anything.
- Do NOT promise repair costs, pricing, timelines, coverage, or appointments — a Birchwood advisor confirms all of that on the callback. If asked, say the advisor will go over those details.
- If the caller mentions anyone was hurt or injured, gently tell them to call 9 1 1 or get medical help if needed, then continue.
- If the caller asks to talk to a person / be transferred / press 0, set caller_wants_human=true.
- Keep each spoken turn short and conversational (1 to 3 sentences). Ask one thing at a time unless they volunteer more.

OUTPUT: Reply with ONLY a single JSON object — no other text, no markdown fences. Put what you want to SAY next in "response_to_caller". Fill any fields you learned this call; use null for anything unknown. Set ready_to_finalize=true ONLY when every REQUIRED field (and the claim number, if a claim was filed) is captured and you've briefly confirmed the key details. Reply in exactly this shape:
{"response_to_caller": "Thanks - and what's the best email for your confirmation? Could you spell it out for me?", "caller_name": "Jane Doe", "phone": null, "email": null, "license_plate": null, "preferred_location": null, "vehicle_year": null, "vehicle_make": "Honda", "vehicle_model": null, "damage_type": null, "is_drivable": null, "filing_insurance_claim": null, "claim_number": null, "caller_wants_human": false, "ready_to_finalize": false}"""


def _claim_was_filed(fields: dict) -> bool:
    """True if the caller said they have opened an MPI claim."""
    raw = str(fields.get("filing_insurance_claim") or "").strip().lower()
    return raw.startswith(("yes", "true", "y ")) or raw in {"y", "1", "true"}


def missing_required_fields(fields: dict) -> list[str]:
    """Required field keys not yet captured (empty/whitespace counts as missing).

    The MPI claim number is required ONLY when the caller said a claim was filed.
    """
    missing = [
        name for name in REQUIRED_FIELDS if not str(fields.get(name) or "").strip()
    ]
    if _claim_was_filed(fields) and not str(fields.get("claim_number") or "").strip():
        missing.append("claim_number")
    return missing


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
    # Latency: only send the last MAX_HISTORY_TURNS exchanges. The captured-state
    # block above carries everything already collected, so older turns add prompt
    # size (and DeepSeek empty-risk) without adding information.
    history = [t for t in session.conversation if t.text]
    for turn in history[-MAX_HISTORY_TURNS:]:
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
        max_tokens=512,
        temperature=0.4,
        # Latency: go straight to a plain completion (the prompt already demands a
        # JSON-only reply) to sidestep DeepSeek's JSON-mode empty/whitespace stalls
        # and their retry penalty — the main cause of the 4-5s turns.
        json_mode=False,
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
        "email": output.email,
        "license_plate": output.license_plate,
        # The intake/record key for the caller's preferred Birchwood location.
        "preferred_collision_center": output.preferred_location,
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


# Short replies that are NOT a name even though they are alphabetic — so we don't
# capture "yes"/"no"/"hello" as the caller's name when the LLM missed it.
_NON_NAME_WORDS = {
    "yes",
    "yeah",
    "yep",
    "yup",
    "no",
    "nope",
    "nah",
    "sure",
    "okay",
    "ok",
    "hello",
    "hi",
    "hey",
    "transfer",
    "operator",
    "agent",
    "human",
    "help",
    "what",
    "sorry",
    "nothing",
    "none",
    "maybe",
    "thanks",
    "thank",
    "please",
}


def _looks_like_name(text: str) -> bool:
    """Conservative check: a short, digit-free, non-filler reply we can treat as a name."""
    text = text.strip()
    words = text.split()
    if not (1 <= len(words) <= 4) or "?" in text:
        return False
    if any(ch.isdigit() for ch in text):
        return False
    if not any(ch.isalpha() for ch in text):
        return False
    # Reject if every word is a known non-name filler (e.g. "no thanks").
    return not all(w.lower().strip(".,!'-") in _NON_NAME_WORDS for w in words)


def _digit_count(text: str) -> int:
    return len(re.sub(r"\D", "", text or ""))


# Spoken digits — phone numbers are very often transcribed as words ("four three
# one ... oh one") rather than digits, so a raw \d count misses them entirely.
_NUM_WORDS = {
    "zero": "0",
    "oh": "0",
    "o": "0",
    "nought": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}


def _spoken_phone_digits(text: str) -> str:
    """Digit string from a reply, reading both literal digits and number-words."""
    out: list[str] = []
    for tok in re.findall(r"[a-z]+|\d", (text or "").lower()):
        if tok.isdigit():
            out.append(tok)
        elif tok in _NUM_WORDS:
            out.append(_NUM_WORDS[tok])
    return "".join(out)


def _asked_for_phone(last_q: str) -> bool:
    """Was the assistant's last question asking for the callback phone number?

    Excludes the MPI *claim* / *policy* number question, which also says "number"
    — we never want to log a claim number as the caller's phone.
    """
    if any(k in last_q for k in ("phone", "callback", "call you back", "reach you")):
        return True
    return "number" in last_q and "claim" not in last_q and "policy" not in last_q


def _last_assistant_question(session: OrchestratorSession) -> str:
    for turn in reversed(session.conversation):
        if turn.role == "assistant" and turn.text:
            return turn.text.lower()
    return ""


def reconstruct_spelled_word(text: str) -> str:
    """Reconstruct a name the caller spelled out letter by letter.

    "Q L I R I M" -> "Qlirim"; "Q L I R I M Tene" -> "Qlirim Tene". Returns "" when
    the reply isn't spelled out (fewer than 3 single-letter tokens).
    """
    tokens = re.findall(r"[a-zA-Z]+", text or "")
    if sum(1 for t in tokens if len(t) == 1) < 3:
        return ""
    words: list[str] = []
    buf: list[str] = []
    for tok in tokens:
        if len(tok) == 1:
            buf.append(tok)
        else:
            if buf:
                words.append("".join(buf))
                buf = []
            words.append(tok)
    if buf:
        words.append("".join(buf))
    return " ".join(w.capitalize() for w in words)


_EMAIL_WORD_REPLACEMENTS = [
    (re.compile(r"\b(at sign|at)\b"), " @ "),
    (re.compile(r"\b(dot|period|point)\b"), " . "),
    (re.compile(r"\b(underscore|under score)\b"), " _ "),
    (re.compile(r"\b(dash|hyphen|minus)\b"), " - "),
    (re.compile(r"\b(plus)\b"), " + "),
]
_EMAIL_SHAPE = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}")


def reconstruct_email(text: str) -> str:
    """Reconstruct an email from a spelled / spoken reply.

    "q l i r i m at gmail dot com" and "qlirim at gmail dot com" both ->
    "qlirim@gmail.com". Single letters are kept literal (so "j o h n" -> "john",
    never "j0hn"). Returns "" when the result isn't a plausible email.
    """
    t = (text or "").lower()
    for pat, rep in _EMAIL_WORD_REPLACEMENTS:
        t = pat.sub(rep, t)
    email = "".join(re.findall(r"@|\.|_|-|\+|[a-z0-9]+", t))
    match = _EMAIL_SHAPE.fullmatch(email)
    return email if match else ""


def _asked_for_email(last_q: str) -> bool:
    return "email" in last_q or "e-mail" in last_q


def _asked_for_plate(last_q: str) -> bool:
    return "plate" in last_q or "licence" in last_q or "license" in last_q


# Spoken digits for a licence plate — exclude the "oh"/"o" homophones (a plate's
# "O" is the letter, not a zero).
_PLATE_DIGIT_WORDS = {k: v for k, v in _NUM_WORDS.items() if k not in ("oh", "o")}


def reconstruct_plate(text: str) -> str:
    """Reconstruct a licence plate from a spoken/spelled reply -> 'ABC123' form."""
    out: list[str] = []
    for tok in re.findall(r"[a-z]+|\d", (text or "").lower()):
        if tok.isdigit():
            out.append(tok)
        elif tok in _PLATE_DIGIT_WORDS:
            out.append(_PLATE_DIGIT_WORDS[tok])
        else:
            out.append(tok)
    return "".join(out).upper()


# --- BI extraction from the damage description ----------------------------------
_BI_AREA_KEYWORDS = {
    "front": ["front", "bumper", "hood", "headlight", "grille", "fender"],
    "rear": ["rear", "back", "trunk", "tailgate", "tail light", "taillight"],
    "driver_side": ["driver side", "driver's side", "left side", "left door"],
    "passenger_side": ["passenger side", "right side", "right door"],
    "side": ["side", "door", "quarter panel"],
    "roof": ["roof", "rollover", "rolled over"],
    "glass": ["windshield", "windscreen", "window", "glass", "mirror"],
    "wheel": ["wheel", "tire", "tyre", "axle", "suspension", "rim"],
}
_BI_COLLISION_KEYWORDS = {
    "rear_end": ["rear-end", "rear end", "rear ended", "rear-ended", "from behind"],
    "head_on": ["head-on", "head on", "frontal"],
    "sideswipe": ["sideswipe", "side-swipe", "swiped"],
    "t_bone": ["t-bone", "t bone", "broadside", "ran a red", "ran the red"],
    "single_vehicle": ["pole", "tree", "curb", "guardrail", "ditch", "deer", "animal"],
    "parking": ["parking lot", "backed into", "while parked", "parked car"],
    "weather": ["hail", "storm", "ice", "black ice", "snow"],
}
_BI_SEVERE = [
    "totaled",
    "total loss",
    "airbag",
    "undrivable",
    "won't start",
    "wont start",
    "smashed",
    "crushed",
    "rolled",
    "severe",
    "major",
    "wrecked",
    "totalled",
]
_BI_MINOR = ["scratch", "scuff", "ding", "dent", "minor", "small", "cosmetic", "chip"]


def extract_damage_bi(damage_text: str | None, fields: dict | None = None) -> dict:
    """Deterministic BI data points derived from the free-text damage description."""
    parts = [str(damage_text or "")]
    if fields:
        parts.append(str(fields.get("damage_type") or ""))
        parts.append(str(fields.get("incident_description") or ""))
    text = " ".join(parts).lower()

    areas = sorted(
        area for area, kws in _BI_AREA_KEYWORDS.items() if any(k in text for k in kws)
    )
    collision_type = next(
        (
            ct
            for ct, kws in _BI_COLLISION_KEYWORDS.items()
            if any(k in text for k in kws)
        ),
        None,
    )
    if any(k in text for k in _BI_SEVERE):
        severity = "severe"
    elif any(k in text for k in _BI_MINOR):
        severity = "minor"
    else:
        severity = "moderate" if text.strip() else None

    drivable = None
    if fields is not None:
        drivable = str(fields.get("is_drivable") or "").strip().lower() or None

    return {
        "severity": severity,
        "impact_areas": areas,
        "collision_type": collision_type,
        "glass_involved": "glass" in areas,
        "airbags_deployed": "airbag" in text,
        "drivable": drivable,
    }


def capture_missing_identity_fields(
    session: OrchestratorSession, user_text: str, output: BirchwoodTurnOutput
) -> None:
    """Deterministic safety net for the fields LLM extraction (and STT) slip on most:
    name, phone, email, and licence plate.

    When the assistant just asked for one of these and the caller answered but the
    model returned it empty, we capture the caller's OWN words instead of asking
    forever. Conservative guards (filler stop-list for names, a 7-digit floor for
    phones, an email-shape check, a 2-8 char plate) avoid mis-capturing a refusal or
    an unrelated reply. The LLM remains the primary path; this only fires on a blank.
    """
    text = (user_text or "").strip()
    if not text:
        return
    scripted = session.channel_metadata.setdefault("scripted_intake", {})
    fields = scripted.setdefault("fields", {})
    last_q = _last_assistant_question(session)

    # NAME — spelled out (foreign / unusual) or spoken plainly.
    if "name" in last_q and not str(fields.get("caller_name") or "").strip():
        spelled = reconstruct_spelled_word(text)
        if spelled:
            fields["caller_name"] = spelled
        elif _looks_like_name(text):
            fields["caller_name"] = text

    # PHONE — digits or spoken number-words.
    if _asked_for_phone(last_q) and not str(fields.get("phone") or "").strip():
        digits = _spoken_phone_digits(text)
        if len(digits) >= 7:
            # Keep the caller's original when it's already digit-formatted;
            # otherwise store the digits we reconstructed from their spoken words.
            fields["phone"] = text if _digit_count(text) >= 7 else digits

    # EMAIL — reconstruct from the spelled / spoken reply.
    if _asked_for_email(last_q) and not str(fields.get("email") or "").strip():
        email = reconstruct_email(text)
        if email:
            fields["email"] = email

    # LICENCE PLATE — spoken or spelled alphanumeric.
    if _asked_for_plate(last_q) and not str(fields.get("license_plate") or "").strip():
        plate = reconstruct_plate(text)
        if 2 <= len(plate) <= 8:
            fields["license_plate"] = plate


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
