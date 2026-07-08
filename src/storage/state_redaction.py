"""At-rest redaction for the serialized session blob (STORE_PHI=false).

``save_session`` persists the full ``OrchestratorSession`` into the
``metadata_json`` column, so with ``STORE_PHI=false`` the turn-row masking in
``_sync_turns`` was partly cosmetic: caller name, callback phone, plate,
transcript text and extraction entities all still landed at rest via the
session blob.

``mask_phi()`` alone cannot make the blob safe:

* it is contextual/regex-based, so a bare structured value
  (``"caller_name": "John Smith"``) or a composed sentence ("Thanks John
  Smith, we'll call you back") sails straight through it, and
* deep-applying it to EVERY string would corrupt machine state — its DOB
  pattern eats ISO dates, which breaks ``model_validate`` on rehydration.

So this module walks the serialized dict with targeted treatments:

* **identity keys** (name / phone / email / plate / ...) — the value is
  replaced outright with ``[REDACTED]``; containers under an identity key are
  walked in force mode so every string inside is redacted;
* **free-text keys** (user_text / system_response / text / summary / ...) —
  ``mask_phi()``, the same treatment the ``triage_turns`` rows already get;
* **entity containers** (extracted_entities / entities / fields) — every
  string value inside gets at least ``mask_phi()``, identity-keyed ones are
  fully redacted (entity keys are open-ended, so unknown keys must not mean
  unmasked values);
* **literal scrub** — the values captured under identity keys are the PII we
  KNOW about (the caller dictated them), so every free-text/entity string is
  additionally scrubbed of those exact literals. This catches what no regex
  can: the caller's name echoed mid-sentence in an assistant reply. The
  scrub deliberately does NOT touch machine-state strings (workflow ids,
  stages, dispositions, rule ids): caller-dictated values must never corrupt
  the persisted routing/audit record (a caller may truthfully be named
  "Birchwood"), and enum-typed fields must survive revalidation.

Values are only ever replaced with strings, never removed, so the redacted
state remains ``OrchestratorSession.model_validate``-able (webhook
redeliveries can still rehydrate a finalized session for idempotent replay).

Deliberately NOT redacted:

* ``call_sid`` / the ``caller_id`` column — an opaque Twilio resource id used
  for webhook-redelivery correlation. It is not the caller's phone number;
  the voice layer never reads Twilio's ``From`` field, so the caller's real
  number is never stored anywhere unless the caller dictates a callback
  number (which IS redacted, via the identity keys).
* operational vehicle facts (year/make/model, drivability) and timestamps —
  not direct identifiers, and masking timestamps would break deserialization.
"""

from __future__ import annotations

import re
from typing import Any

from src.safety.phi_masking import mask_phi

_MASK = "[REDACTED]"

# Identity literals shorter than this are not scrubbed from free text — a
# 1-3 char value ("no", "MB") would shred unrelated words. Structured values
# under identity keys are still replaced outright regardless of length.
_MIN_SCRUB_LEN = 4

# Structured values that ARE a direct personal identifier. A bare value under
# one of these keys is unmaskable by regex, so it is replaced outright.
IDENTITY_KEYS = frozenset(
    {
        "name",
        "full_name",
        "first_name",
        "last_name",
        "caller_name",
        "customer_name",
        "patient_name",
        "contact_name",
        "phone",
        "phone_number",
        "callback_number",
        "contact_phone",
        "best_phone",
        "email",
        "email_address",
        "address",
        "mailing_address",
        "street_address",
        "incident_location",
        "license_plate",
        "plate",
        "vin",
        "claim_number",
        "policy_number",
        "date_of_birth",
        "dob",
        "drivers_license",
        "driver_license",
        "license_number",
        "licence_number",
    }
)

# Free text spoken by or to the caller — masked with mask_phi so the shape
# survives (audit) but detected PHI does not. Same policy as triage_turns.
# Includes the audit-trace summaries (verbatim caller utterances — the
# orchestrator's _redact only truncates, it does not mask), the SBAR /
# finalize narrative fields, and the healthcare intake narrative fields:
# all of these carry spoken PHI that no identity key captures.
FREE_TEXT_KEYS = frozenset(
    {
        "user_text",
        "system_response",
        "system_text",
        "assistant_text",
        "response_to_caller",
        "text",
        "content",
        "summary",
        "chief_complaint",
        "incident_description",
        "damage_description",
        "damage_type",
        "dynamic_text",
        "notes",
        "raw_user_input",
        # audit trail (verbatim utterance excerpts)
        "input_summary",
        "output_summary",
        # finalize / SBAR narrative
        "sbar_report",
        "patient_summary",
        "disposition_reasoning",
        "situation",
        "background",
        "assessment",
        "recommendation",
        # healthcare intake narrative ("123 Main Street" lives in location)
        "location",
        "onset_time",
        "relevant_history",
        "meds",
        "allergies",
        # report/extraction artifacts
        "reasoning",
        "reason_for_audit",
        "recommended_actions",
        # safety-flag text: deterministic flags are rule-id joins (mask_phi
        # is a no-op on them; the literal scrub only bites if a captured
        # identity value is a substring of a rule id — accepted as remote),
        # but LLM-sourced flags describe caller utterances verbatim.
        "flag",
        "script_to_say",
        "flags",
    }
)

# Sentinel values routinely stored under identity keys when nothing was
# captured. They carry no PHI, must not be force-redacted (losing the "we
# don't know" audit signal), and above all must never become scrub literals —
# a "name" of "Unknown" would shred the word "unknown" across every free-text
# string in the record.
PLACEHOLDER_VALUES = frozenset({"unknown", "not documented", "none", "n/a", ""})


def _is_placeholder(value: str) -> bool:
    return value.strip().lower() in PLACEHOLDER_VALUES


# Containers whose keys are open-ended entity names: every string inside gets
# at least mask_phi, so an unanticipated entity key never means an unmasked
# value.
ENTITY_CONTAINER_KEYS = frozenset({"extracted_entities", "entities", "fields"})


def _is_identity(key: str | None) -> bool:
    return key is not None and key.lower() in IDENTITY_KEYS


def collect_identity_values(value: Any, key: str | None = None) -> set[str]:
    """All string values stored under identity keys, anywhere in the state.

    These are the caller-dictated identifiers the system explicitly captured
    — the ground truth for the literal scrub.
    """
    found: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            child_key = k if isinstance(k, str) else None
            if _is_identity(child_key):
                found.update(_strings_within(v))
            else:
                found.update(collect_identity_values(v, child_key))
    elif isinstance(value, list):
        for item in value:
            found.update(collect_identity_values(item, key))
    elif isinstance(value, str) and _is_identity(key):
        found.add(value)
    return {
        v.strip()
        for v in found
        if len(v.strip()) >= _MIN_SCRUB_LEN and not _is_placeholder(v)
    }


def _strings_within(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        out: set[str] = set()
        for v in value.values():
            out.update(_strings_within(v))
        return out
    if isinstance(value, list):
        out = set()
        for item in value:
            out.update(_strings_within(item))
        return out
    return set()


def _literal_pattern(literals: set[str]) -> re.Pattern | None:
    if not literals:
        return None
    ordered = sorted(literals, key=len, reverse=True)
    return re.compile("|".join(re.escape(lit) for lit in ordered), re.IGNORECASE)


def scrub_identity_literals(text: str, literals: set[str]) -> str:
    """Replace known identity literals (case-insensitive) in free text."""
    pattern = _literal_pattern(literals)
    if not text or pattern is None:
        return text
    return pattern.sub(_MASK, text)


def redact_session_state(value: Any, key: str | None = None) -> Any:
    """Return a redacted deep copy of a serialized-session value."""
    literals = collect_identity_values(value, key)
    pattern = _literal_pattern(literals)
    return _walk(
        value,
        key,
        pattern,
        force=_is_identity(key),
        soft=key is not None and key.lower() in ENTITY_CONTAINER_KEYS,
    )


def _walk(
    value: Any,
    key: str | None,
    pattern: re.Pattern | None,
    *,
    force: bool,
    soft: bool,
) -> Any:
    """``force`` — below an identity key: all strings -> [REDACTED].
    ``soft`` — below an entity container: strings -> mask_phi at minimum.
    Both flags only ever tighten, never loosen."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            child_key = k if isinstance(k, str) else None
            out[k] = _walk(
                v,
                child_key,
                pattern,
                force=force or _is_identity(child_key),
                soft=soft
                or (
                    child_key is not None and child_key.lower() in ENTITY_CONTAINER_KEYS
                ),
            )
        return out
    if isinstance(value, list):
        return [_walk(item, key, pattern, force=force, soft=soft) for item in value]
    if isinstance(value, str):
        if not value:
            return value
        if force or _is_identity(key):
            # "Unknown"/"not documented" sentinels carry no PHI — keep them
            # so the record still says the field was never captured.
            if _is_placeholder(value):
                return value
            return _MASK
        if soft or (key is not None and key.lower() in FREE_TEXT_KEYS):
            # Free-text/entity strings get mask_phi AND the literal scrub.
            # Machine-state strings (workflow_id, stage, dispositions, rule
            # ids, replay TwiML keys) are deliberately NOT scrubbed: a caller
            # whose dictated name is a substring of one of them (e.g.
            # "Birchwood") must not be able to corrupt the persisted routing
            # or audit record, and enum-typed fields must stay
            # model_validate()-able.
            masked = mask_phi(value)
            if pattern is not None:
                masked = pattern.sub(_MASK, masked)
            return masked
        return value
    return value
