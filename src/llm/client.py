"""
DeepSeek Structured Client Wrapper

Thin wrapper around AsyncOpenAI that enforces:
- Timeouts
- Exponential backoff retries
- JSON-only output with schema validation
- Automatic single-pass JSON repair
- Fail-safe escalation on persistent failure
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional, Type, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from src.llm.config import (
    DEEPSEEK_BASE_URL,
    DEEPSEEK_API_KEY,
    DEEPSEEK_MODEL,
    LLM_TIMEOUT,
)
from src.observability.sentry_integration import (
    capture_llm_failure,
    capture_json_validation_failure,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMCallError(Exception):
    """Raised when the LLM call fails after retries and repair."""

    pass


class StructuredLLMClient:
    """Wrapper for DeepSeek that guarantees structured JSON outputs.

    Usage:
        client = get_structured_client()
        result = await client.call(
            messages=[...],
            output_schema=IntakeTurnOutput,
            max_tokens=500,
        )
    """

    def __init__(self) -> None:
        # HARD FAIL: production requires a real API key
        from src.config import ENVIRONMENT

        if ENVIRONMENT == "production" and not DEEPSEEK_API_KEY:
            raise RuntimeError(
                "Production requires DEEPSEEK_API_KEY. "
                "Cannot start without LLM credentials."
            )
        self._client = AsyncOpenAI(
            base_url=DEEPSEEK_BASE_URL,
            api_key=DEEPSEEK_API_KEY,
            timeout=LLM_TIMEOUT,
        )
        self._model = DEEPSEEK_MODEL
        logger.info(
            f"[LLM] StructuredLLMClient initialized (model={self._model}, "
            f"timeout={LLM_TIMEOUT}s)"
        )

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((Exception,)),
        reraise=True,
    )
    async def _raw_call(
        self,
        messages: list[dict],
        max_tokens: int = 500,
        temperature: float = 0.3,
        json_mode: bool = False,
    ) -> str:
        """Make a raw chat completion call and return the text content.

        Retries up to 2 attempts with exponential backoff.

        Args:
            json_mode: If True, request JSON output from the model.
                       Eliminates need for repair cycles.
        """
        t0 = time.monotonic()
        kwargs: dict = dict(
            model=self._model,
            messages=messages,  # type: ignore
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = await self._client.chat.completions.create(**kwargs)
        elapsed = (time.monotonic() - t0) * 1000
        content = response.choices[0].message.content or ""
        logger.info(
            f"[LLM] Raw call completed in {elapsed:.0f}ms, "
            f"response length={len(content)}"
        )
        return content.strip()

    async def call(
        self,
        messages: list[dict],
        output_schema: Type[T],
        max_tokens: int = 500,
        temperature: float = 0.3,
        correlation_id: str | None = None,
        json_mode: bool = True,
    ) -> T:
        """Call DeepSeek and return a validated Pydantic object.

        Flow:
        1. Make raw call
        2. Extract JSON from response
        3. Validate against schema
        4. If invalid: run single repair pass
        5. If still invalid: raise LLMCallError

        Args:
            messages: Chat messages (system + user/assistant history).
            output_schema: Pydantic model to validate against.
            max_tokens: Maximum response tokens.
            temperature: Sampling temperature.
            correlation_id: Optional ID for log correlation.

        Returns:
            Validated Pydantic model instance.

        Raises:
            LLMCallError: If validation fails after repair attempt.
        """
        cid = correlation_id or "no-cid"

        # Step 1: raw call. DeepSeek intermittently returns an EMPTY / whitespace-only
        # completion in JSON mode (it goes away and comes back under load), and that
        # empty also costs a full round-trip plus retries — the main latency hit. So
        # the conversational caller passes json_mode=False to go straight to a plain
        # completion (the prompt demands JSON-only and _try_parse extracts it),
        # avoiding the JSON-mode empties entirely. We try the chosen mode twice, then
        # fall back to the other mode before giving up.
        attempt_modes = [json_mode, json_mode, not json_mode]
        try:
            raw = ""
            for i, mode in enumerate(attempt_modes):
                raw = await self._raw_call(
                    messages, max_tokens, temperature, json_mode=mode
                )
                if raw:
                    break
                if i == 0:
                    logger.warning(f"[LLM:{cid}] Empty response; retrying")
                elif i == 1:
                    logger.warning(f"[LLM:{cid}] Still empty; trying alternate mode")
        except Exception as e:
            logger.error(f"[LLM:{cid}] Raw call failed after retries: {e}")
            capture_llm_failure(
                model_name=self._model,
                timeout_duration=LLM_TIMEOUT,
                retry_count=2,
                error_type=type(e).__name__,
            )
            raise LLMCallError(f"LLM call failed: {e}") from e

        if not raw:
            logger.warning(f"[LLM:{cid}] Empty response from DeepSeek after retries")
            raise LLMCallError("Empty response from LLM")

        # Step 2+3: parse and validate
        parsed = self._try_parse(raw, output_schema, cid)
        if parsed is not None:
            return parsed

        # Step 4: repair pass
        logger.warning(f"[LLM:{cid}] First parse failed, attempting repair")
        repaired = await self._repair(messages, raw, output_schema, max_tokens, cid)
        if repaired is not None:
            return repaired

        # Step 5: fail
        logger.error(f"[LLM:{cid}] Repair also failed — raising LLMCallError")
        capture_json_validation_failure(
            schema_name=output_schema.__name__,
            error_message="JSON validation failed after repair attempt",
        )
        raise LLMCallError("JSON validation failed after repair attempt")

    def _try_parse(
        self,
        raw: str,
        schema: Type[T],
        cid: str,
    ) -> Optional[T]:
        """Try to extract and validate JSON from raw LLM output."""
        # Strip markdown fences
        cleaned = raw
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-z]*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)

        # Find JSON object
        json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not json_match:
            logger.warning(f"[LLM:{cid}] No JSON object found in response")
            return None

        try:
            data = json.loads(json_match.group(0))
        except json.JSONDecodeError as e:
            logger.warning(f"[LLM:{cid}] JSON decode error: {e}")
            return None

        try:
            return schema.model_validate(data)
        except ValidationError as e:
            logger.warning(f"[LLM:{cid}] Schema validation error: {e}")
            return None

    async def _repair(
        self,
        original_messages: list[dict],
        bad_output: str,
        schema: Type[T],
        max_tokens: int,
        cid: str,
    ) -> Optional[T]:
        """Attempt a single repair call asking the LLM to fix its JSON."""
        schema_json = schema.model_json_schema()
        repair_prompt = (
            "Your previous response was not valid JSON matching the required schema. "
            "Fix it NOW. Return ONLY a single JSON object, no markdown, no explanations.\n\n"
            f"Required schema:\n{json.dumps(schema_json, indent=2)}\n\n"
            f"Your broken output was:\n{bad_output[:1000]}\n\n"
            "Return ONLY the corrected JSON object."
        )

        repair_messages = original_messages + [
            {"role": "assistant", "content": bad_output[:500]},
            {"role": "user", "content": repair_prompt},
        ]

        try:
            raw = await self._raw_call(
                repair_messages, max_tokens, temperature=0.1, json_mode=True
            )
        except Exception as e:
            logger.error(f"[LLM:{cid}] Repair call failed: {e}")
            return None

        return self._try_parse(raw, schema, cid)


# Singleton
_structured_client: StructuredLLMClient | None = None


def get_structured_client() -> StructuredLLMClient:
    """Get or create the singleton StructuredLLMClient."""
    global _structured_client
    if _structured_client is None:
        _structured_client = StructuredLLMClient()
    return _structured_client
