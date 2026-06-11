"""Enrichment features (shadow mode) — each independently flag-gated.

A feature = output schema + prompt builder + deterministic postprocess.
Prompts receive REDACTED text only (per ENRICHMENT_PII_MODE); deterministic
postprocessing applies safety overrides the LLM cannot undo (an
injury-flagged record is always urgent, follow-up drafts always require
human approval).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Type

from pydantic import BaseModel, Field

import src.config as config

# ---------------------------------------------------------------------------
# Output schemas (validated via the provider's structured-output contract)
# ---------------------------------------------------------------------------


class NormalizedVehicle(BaseModel):
    year: int | None = None
    make: str | None = None
    model: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class NormalizedDamage(BaseModel):
    locations: list[str] = Field(default_factory=list)
    severity: Literal["minor", "moderate", "severe", "unknown"] = "unknown"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class NormalizeOutput(BaseModel):
    """Feature 1 — cleaned, normalized record fields."""

    vehicle: NormalizedVehicle = Field(default_factory=NormalizedVehicle)
    damage: NormalizedDamage = Field(default_factory=NormalizedDamage)
    incident_location_normalized: str | None = None
    incident_datetime_normalized: str | None = None
    insurance_provider_normalized: str | None = None
    claim_reference_format_valid: bool | None = None
    fields_needing_review: list[str] = Field(default_factory=list)
    notes: str = ""


class SummaryOutput(BaseModel):
    """Feature 2 — polished shop-facing prose summary."""

    headline: str
    summary: str
    recommended_next_action: str


class InjuryBranchQA(BaseModel):
    injury_mentioned_in_transcript: bool
    advisory_present_in_transcript: bool
    handled_correctly: bool
    notes: str = ""


class QAOutput(BaseModel):
    """Feature 3 — per-call quality analysis for our own pilot review."""

    intake_completed: bool
    friction_points: list[str] = Field(default_factory=list)
    caller_sentiment: Literal[
        "calm", "neutral", "frustrated", "distressed", "unknown"
    ] = "unknown"
    injury_branch: InjuryBranchQA
    fields_captured_poorly: list[str] = Field(default_factory=list)
    improvement_notes: list[str] = Field(default_factory=list)


class FollowupOutput(BaseModel):
    """Feature 4 — DRAFT follow-up message (never auto-sent)."""

    sms_draft: str
    email_subject: str
    email_draft: str
    requires_human_approval: bool = True


class RoutingOutput(BaseModel):
    """Feature 5 — routing/prioritization suggestion (recommendation only)."""

    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    suggested_location: str | None = None
    suggested_department: Literal[
        "collision_center", "glass_department", "service", "unknown"
    ] = "unknown"
    rationale: str = ""


# ---------------------------------------------------------------------------
# Prompt builders — context is ALREADY redacted per ENRICHMENT_PII_MODE
# ---------------------------------------------------------------------------

_SYSTEM = (
    "You are a post-call analyst for Birchwood Automotive Group's collision "
    "intake line. You receive a completed call's structured fields and "
    "transcript. Direct identifiers may appear as tokens like "
    "[CUSTOMER_NAME] or [PHONE_1]; treat them as opaque placeholders and "
    "reuse them verbatim where natural. Respond ONLY with a JSON object "
    "matching the requested schema. Never invent facts not supported by "
    "the call."
)


def _ctx_block(context: dict[str, Any]) -> str:
    return (
        f"STRUCTURED FIELDS (redaction-safe):\n{context['fields_text']}\n\n"
        f"OUTCOME: {context['outcome']}  FLAGS: {context['flags']}\n\n"
        f"TRANSCRIPT:\n{context['transcript_text']}"
    )


def _normalize_prompt(context: dict[str, Any]) -> list[dict]:
    return [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                "Clean and normalize this collision intake record. Correct "
                "obvious speech-to-text artifacts (vehicle make/model/year, "
                "place names), infer damage locations and severity from the "
                "narrative, normalize the incident date/time to ISO-ish "
                "phrasing if possible, normalize the insurance provider "
                "name, and judge whether the claim reference looks like a "
                "plausible format. List any field you are not confident "
                "about in fields_needing_review.\n\n"
                f"{_ctx_block(context)}\n\n"
                "JSON schema keys: vehicle{year,make,model,confidence}, "
                "damage{locations,severity(minor|moderate|severe|unknown),"
                "confidence}, incident_location_normalized, "
                "incident_datetime_normalized, insurance_provider_normalized, "
                "claim_reference_format_valid, fields_needing_review, notes."
            ),
        },
    ]


def _summary_prompt(context: dict[str, Any]) -> list[dict]:
    return [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                "Write a polished, professional intake summary for "
                "Birchwood's service team — the collision equivalent of a "
                "clinical handoff. Plain prose, 4-7 sentences: what "
                "happened, the vehicle and drivability, any injury flag, "
                "insurance status, and the recommended next action. Calm, "
                "concrete, no fluff, no speculation.\n\n"
                f"{_ctx_block(context)}\n\n"
                "JSON schema keys: headline (one line), summary (prose), "
                "recommended_next_action."
            ),
        },
    ]


def _qa_prompt(context: dict[str, Any]) -> list[dict]:
    return [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                "Analyze this call's QUALITY for the intake team's own "
                "review. Did the intake complete? Where did the caller "
                "hesitate, repeat themselves, or get re-prompted? What was "
                "the caller's apparent sentiment? Did the transcript "
                "mention injuries, and does the transcript contain the "
                "medical-attention/9-1-1 advisory? Which fields look "
                "poorly captured (STT garble, wrong format)?\n\n"
                f"{_ctx_block(context)}\n\n"
                "JSON schema keys: intake_completed, friction_points, "
                "caller_sentiment(calm|neutral|frustrated|distressed|"
                "unknown), injury_branch{injury_mentioned_in_transcript,"
                "advisory_present_in_transcript,handled_correctly,notes}, "
                "fields_captured_poorly, improvement_notes."
            ),
        },
    ]


def _followup_prompt(context: dict[str, Any]) -> list[dict]:
    return [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                "Draft a follow-up message to the customer in Birchwood "
                "Automotive Group's warm service-center voice, summarizing "
                "what was captured and the next step (an advisor will call "
                "back; no coverage/pricing/appointment commitments). "
                "Address the customer as [CUSTOMER_NAME]. Produce a short "
                "SMS variant and an email variant. These are DRAFTS for "
                "human review - do not imply anything has been booked.\n\n"
                f"{_ctx_block(context)}\n\n"
                "JSON schema keys: sms_draft, email_subject, email_draft, "
                "requires_human_approval (always true)."
            ),
        },
    ]


def _routing_prompt(context: dict[str, Any]) -> list[dict]:
    return [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                "Suggest a priority level and the best-fit Birchwood "
                "location/department for this intake. This is a "
                "RECOMMENDATION for a human dispatcher, never an automated "
                "action. Consider drivability, damage severity, glass-only "
                "vs body work, luxury brand handling, and completeness.\n\n"
                f"AVAILABLE LOCATIONS: {context['locations']}\n\n"
                f"{_ctx_block(context)}\n\n"
                "JSON schema keys: priority(low|normal|high|urgent), "
                "suggested_location, suggested_department(collision_center|"
                "glass_department|service|unknown), rationale."
            ),
        },
    ]


# ---------------------------------------------------------------------------
# Deterministic postprocessing — overrides the LLM cannot undo
# ---------------------------------------------------------------------------


def _post_routing(output: RoutingOutput, context: dict[str, Any]) -> RoutingOutput:
    if context.get("injury_flagged"):
        if output.priority != "urgent":
            output.rationale = (
                "DETERMINISTIC OVERRIDE: record is injury-flagged — priority "
                "forced to urgent (LLM suggested "
                f"{output.priority}). " + output.rationale
            )
        output.priority = "urgent"
    return output


def _post_followup(output: FollowupOutput, context: dict[str, Any]) -> FollowupOutput:
    output.requires_human_approval = True  # non-negotiable
    return output


def _post_qa(output: QAOutput, context: dict[str, Any]) -> QAOutput:
    # The deterministic record is ground truth for the injury branch; the
    # LLM only assesses the transcript. Disagreement = needs review.
    if context.get("injury_flagged") and not (
        output.injury_branch.injury_mentioned_in_transcript
    ):
        output.injury_branch.handled_correctly = False
        output.injury_branch.notes = (
            "MISMATCH: record is injury-flagged but the analysis did not "
            "find the mention - review manually. " + output.injury_branch.notes
        )
    return output


def _post_identity(output: BaseModel, context: dict[str, Any]) -> BaseModel:
    return output


@dataclass(frozen=True)
class EnrichmentFeature:
    name: str
    flag_attr: str
    schema: Type[BaseModel]
    build_prompt: Callable[[dict[str, Any]], list[dict]]
    postprocess: Callable[[Any, dict[str, Any]], BaseModel]
    max_tokens: int = 800


PER_CALL_FEATURES: list[EnrichmentFeature] = [
    EnrichmentFeature(
        "normalize",
        "ENRICH_NORMALIZE",
        NormalizeOutput,
        _normalize_prompt,
        _post_identity,
    ),
    EnrichmentFeature(
        "summary",
        "ENRICH_SUMMARY",
        SummaryOutput,
        _summary_prompt,
        _post_identity,
        max_tokens=600,
    ),
    EnrichmentFeature(
        "qa",
        "ENRICH_QA",
        QAOutput,
        _qa_prompt,
        _post_qa,
    ),
    EnrichmentFeature(
        "followup",
        "ENRICH_FOLLOWUP",
        FollowupOutput,
        _followup_prompt,
        _post_followup,
        max_tokens=700,
    ),
    EnrichmentFeature(
        "routing",
        "ENRICH_ROUTING",
        RoutingOutput,
        _routing_prompt,
        _post_routing,
        max_tokens=400,
    ),
]


def enabled_features() -> list[EnrichmentFeature]:
    return [
        feature
        for feature in PER_CALL_FEATURES
        if bool(getattr(config, feature.flag_attr, False))
    ]
