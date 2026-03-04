"""
Guarded LLM Client — THE single wrapper for ALL LLM calls.

Hard requirement: No code outside this module may call DeepSeekClient
or StructuredLLMClient directly. Every LLM output passes through
gate_triage_output() or gate_outbound_text() before leaving this module.

Enforce with:
  - test_no_direct_deepseek_usage (static grep)
  - runtime guard in DeepSeekClient (caller check)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Literal, Optional, Type, TypeVar

from pydantic import BaseModel

from src.llm.client import StructuredLLMClient, LLMCallError, get_structured_client
from src.safety.gate import (
    gate_triage_output,
    gate_outbound_text,
    GateContext,
    FinalDecision,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Fields on Pydantic models that contain caller/file-facing text
_OUTBOUND_TEXT_FIELDS = frozenset(
    {
        "next_question",
        "response_to_caller",
        "patient_summary",
        "sbar_report",
        "disposition_reasoning",
        "safety_net_instructions",
        "script_to_say",
        "flag",
        "reason_for_audit",
    }
)


@dataclass
class GuardedLLM:
    """Single entry point for ALL LLM interactions.

    Every method ensures the output passes through the unified gate
    before it can be used downstream.
    """

    _client: StructuredLLMClient = field(default_factory=get_structured_client)

    # ── Triage decision (JSON → FinalDecision) ────────────────────────────

    async def triage_json(
        self,
        *,
        messages: list[dict],
        ctx: GateContext,
        max_tokens: int = 500,
        temperature: float = 0.3,
        correlation_id: str | None = None,
    ) -> FinalDecision:
        """Call LLM for triage JSON, parse, and gate through gate_triage_output.

        Returns FinalDecision — never raw LLM output.
        On LLM failure: returns fail-closed FinalDecision (HUMAN_REVIEW).
        """
        cid = correlation_id or "no-cid"
        try:
            raw_text = await self._client._raw_call(messages, max_tokens, temperature)
        except Exception as exc:
            logger.error(f"[GUARDED:{cid}] LLM call failed: {exc}")
            return gate_triage_output({}, ctx)  # empty dict → gate will fail-closed

        parsed = _extract_json(raw_text)
        if parsed is None:
            logger.warning(
                f"[GUARDED:{cid}] No JSON in LLM response, gating empty dict"
            )
            parsed = {}

        return gate_triage_output(parsed, ctx)

    # ── Outbound text (raw text → gated text) ────────────────────────────

    async def outbound_text(
        self,
        *,
        messages: list[dict],
        ctx: GateContext,
        kind: Literal["question", "phase1_reply", "handoff"],
        max_tokens: int = 500,
        temperature: float = 0.3,
        correlation_id: str | None = None,
    ) -> str:
        """Call LLM for free-text output, gate through gate_outbound_text.

        Returns gated text — never raw LLM output.
        On LLM failure: returns safe fallback string.
        """
        cid = correlation_id or "no-cid"
        try:
            raw_text = await self._client._raw_call(messages, max_tokens, temperature)
        except Exception as exc:
            logger.error(f"[GUARDED:{cid}] LLM call failed for {kind}: {exc}")
            return _safe_fallback_for_kind(kind)

        if not raw_text or not raw_text.strip():
            logger.warning(f"[GUARDED:{cid}] Empty LLM response for {kind}")
            return _safe_fallback_for_kind(kind)

        return gate_outbound_text(raw_text, ctx, kind)

    # ── Structured call (schema-validated + outbound text fields gated) ───

    async def structured_call(
        self,
        *,
        messages: list[dict],
        output_schema: Type[T],
        ctx: GateContext,
        kind: Literal["question", "phase1_reply", "handoff"] = "question",
        max_tokens: int = 500,
        temperature: float = 0.3,
        correlation_id: str | None = None,
    ) -> T:
        """Call LLM, validate against Pydantic schema, gate outbound text fields.

        Uses StructuredLLMClient.call() for schema validation and repair,
        then gates all outbound text fields through gate_outbound_text().

        Raises LLMCallError if schema validation fails (caller must handle).
        """
        cid = correlation_id or "no-cid"

        result = await self._client.call(
            messages=messages,
            output_schema=output_schema,
            max_tokens=max_tokens,
            temperature=temperature,
            correlation_id=cid,
        )

        # Gate all outbound text fields
        _gate_model_text_fields(result, ctx, kind)

        return result

    # ── Raw call with gate (for repair/Phase1 flows) ─────────────────────

    async def raw_call_gated(
        self,
        *,
        messages: list[dict],
        ctx: GateContext,
        kind: Literal["question", "phase1_reply", "handoff"] = "phase1_reply",
        max_tokens: int = 500,
        temperature: float = 0.3,
    ) -> str:
        """Make raw LLM call, return raw text BUT gated.

        Used for Phase1 validation flows where the caller needs
        the raw JSON string but it still must be gated before any
        text field is sent to the caller.
        """
        try:
            raw = await self._client._raw_call(messages, max_tokens, temperature)
        except Exception as exc:
            logger.error(f"[GUARDED] Raw call failed: {exc}")
            raise LLMCallError(f"LLM call failed: {exc}") from exc

        # Gate the entire raw text (catches diagnosis/unsafe content)
        return gate_outbound_text(raw, ctx, kind)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_guarded_llm: GuardedLLM | None = None


def get_guarded_llm() -> GuardedLLM:
    """Get or create the singleton GuardedLLM instance."""
    global _guarded_llm
    if _guarded_llm is None:
        _guarded_llm = GuardedLLM()
    return _guarded_llm


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> Optional[dict]:
    """Extract JSON object from raw LLM text."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _safe_fallback_for_kind(kind: str) -> str:
    """Return safe fallback text based on output kind."""
    if kind == "question":
        return "Can you tell me more about what you're experiencing?"
    elif kind == "handoff":
        return "Report generation unavailable. Manual review required."
    else:
        return (
            "I want to make sure you get the right care. "
            "A nurse will review your case shortly."
        )


def _gate_model_text_fields(
    model: BaseModel,
    ctx: GateContext,
    kind: Literal["question", "phase1_reply", "handoff"],
) -> None:
    """Gate all outbound text fields on a Pydantic model in-place."""
    klass = model.__class__
    if not hasattr(klass, "model_fields"):
        return  # Not a real Pydantic model (e.g. mock in tests)
    for field_name in klass.model_fields:
        if field_name not in _OUTBOUND_TEXT_FIELDS:
            continue
        value = getattr(model, field_name, None)
        if isinstance(value, str):
            gated = gate_outbound_text(value, ctx, kind)
            try:
                setattr(model, field_name, gated)
            except (AttributeError, ValueError):
                pass  # frozen field
        elif isinstance(value, list):
            gated_list = []
            for item in value:
                if isinstance(item, str):
                    gated_list.append(gate_outbound_text(item, ctx, kind))
                else:
                    gated_list.append(item)
            try:
                setattr(model, field_name, gated_list)
            except (AttributeError, ValueError):
                pass
