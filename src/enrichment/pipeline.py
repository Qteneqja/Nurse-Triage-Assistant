"""Enrichment pipeline — async, after finalize, fail closed and silent.

Entry point: ``run_enrichment_for_session(session_id)``, scheduled as a
background task after a Birchwood call finalizes. Every failure mode
(flags off, wrong vertical, provider down, malformed output, storage
error) is caught here; the source call record is NEVER modified, and a
pipeline crash can never propagate to the caller.

PII: the transcript/field text sent to the provider passes through
redaction per ENRICHMENT_PII_MODE. Logs and stored failure rows carry no
caller PII (session id, feature name, error type only).
"""

from __future__ import annotations

import logging
from typing import Any

import src.config as config
from src.enrichment.features import EnrichmentFeature, enabled_features
from src.enrichment.provider import (
    EnrichmentOutputError,
    get_enrichment_provider,
)
from src.enrichment.redaction import redact_for_provider

logger = logging.getLogger(__name__)

_REDACT_SAFE_FIELDS = [
    "vehicle_year",
    "vehicle_make",
    "vehicle_model",
    "is_drivable",
    "damage_type",
    "incident_datetime",
    "incident_location",
    "injuries_state",
    "other_parties",
    "police_report_filed",
    "photos_available",
    "filing_insurance_claim",
    "insurance_provider",
    "preferred_collision_center",
    "missing_information",
]


def enrichment_enabled_for(session: Any) -> bool:
    """Cheap guard used at the trigger site — false means zero new paths."""
    if not getattr(config, "ENRICHMENT_ENABLED", False):
        return False
    return (session.vertical_key or "") == "automotive_collision"


def _record_of(session: Any) -> dict[str, Any]:
    final = session.channel_metadata.get("workflow_final_result") or {}
    record = (final.get("structured_output") or {}).get("intake_record")
    return record if isinstance(record, dict) else {}


def build_context(session: Any) -> dict[str, Any]:
    """Build the (redacted) feature context from a finalized session."""
    record = _record_of(session)
    mode = getattr(config, "ENRICHMENT_PII_MODE", "redact")

    transcript = "\n".join(
        f"{turn.role}: {turn.text}" for turn in session.conversation if turn.text
    )
    redacted_transcript = redact_for_provider(transcript, record, mode=mode)

    fields_lines = []
    for key in _REDACT_SAFE_FIELDS:
        value = record.get(key)
        if value not in (None, "", []):
            fields_lines.append(f"{key}: {value}")
    narrative = redact_for_provider(
        str(record.get("incident_description") or ""), record, mode=mode
    )
    fields_lines.append(f"incident_description: {narrative.text}")
    if mode == "raw":
        for key in ("caller_name", "phone", "email", "address", "claim_number"):
            if record.get(key):
                fields_lines.append(f"{key}: {record[key]}")

    from src.verticals.automotive_collision.rules import (
        configured_collision_locations,
        configured_luxury_location,
    )

    return {
        "session_id": session.session_id,
        "call_sid": session.call_sid,
        "pii_mode": mode,
        "fields_text": "\n".join(fields_lines),
        "transcript_text": redacted_transcript.text,
        "tokens": redacted_transcript.tokens,
        "outcome": str(record.get("recommended_routing") or "UNKNOWN"),
        "flags": ", ".join(str(f) for f in record.get("flags") or []) or "none",
        "injury_flagged": "injuries_reported" in (record.get("flags") or []),
        "locations": ", ".join(
            [*configured_collision_locations(), configured_luxury_location()]
        ),
    }


async def _run_feature(
    repo: Any,
    provider: Any,
    feature: EnrichmentFeature,
    context: dict[str, Any],
) -> None:
    base = {
        "session_id": context["session_id"],
        "call_sid": context["call_sid"],
        "feature": feature.name,
        "pii_mode": context["pii_mode"],
        "provider": getattr(provider, "name", "unknown"),
        "model": getattr(provider, "model", "unknown"),
        "tokens_map": context["tokens"],
    }
    try:
        output = await provider.call(
            feature.build_prompt(context),
            feature.schema,
            max_tokens=feature.max_tokens,
        )
        output = feature.postprocess(output, context)
        repo.save_enrichment_result(
            {**base, "status": "completed", "payload": output.model_dump(mode="json")}
        )
        logger.info(
            "[ENRICHMENT] %s completed for session %s",
            feature.name,
            context["session_id"],
        )
    except EnrichmentOutputError as exc:
        repo.save_enrichment_result(
            {
                **base,
                "status": "needs_review",
                "payload": {"error": "malformed_output", "detail": str(exc)[:300]},
            }
        )
        logger.warning(
            "[ENRICHMENT] %s output failed validation for session %s "
            "(stored as needs_review)",
            feature.name,
            context["session_id"],
        )
    except Exception as exc:  # provider/transport/anything — fail closed
        repo.save_enrichment_result(
            {
                **base,
                "status": "failed",
                "payload": {"error": type(exc).__name__, "detail": str(exc)[:300]},
            }
        )
        logger.warning(
            "[ENRICHMENT] %s failed for session %s: %s",
            feature.name,
            context["session_id"],
            type(exc).__name__,
        )


async def run_enrichment_for_session(session_id: str) -> None:
    """Run all enabled enrichment features for a finalized session.

    Never raises. Never modifies the source record.
    """
    try:
        if not getattr(config, "ENRICHMENT_ENABLED", False):
            return
        from src.storage.session_repository import get_session_repository

        repo = get_session_repository()
        session = repo.load_session(session_id)
        if session is None or not enrichment_enabled_for(session):
            return
        if not session.is_finalized:
            logger.info("[ENRICHMENT] Session %s not finalized — skipping", session_id)
            return
        features = enabled_features()
        if not features:
            return
        provider = get_enrichment_provider()
        context = build_context(session)
        for feature in features:
            await _run_feature(repo, provider, feature, context)
    except Exception:
        # Absolute backstop: enrichment must never propagate failures.
        logger.warning(
            "[ENRICHMENT] Pipeline error for session %s (fail closed)",
            session_id,
            exc_info=True,
        )
