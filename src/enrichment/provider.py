"""Provider-agnostic LLM interface for the enrichment layer.

The contract mirrors the platform's proven structured-output shape:
``call(messages, output_schema) -> validated pydantic model`` with a single
repair pass, raising on failure. Providers are selected by LLM_PROVIDER and
constructed lazily — with ENRICHMENT_ENABLED=false nothing here ever runs,
and no provider client is instantiated.

The live Birchwood call flow does NOT use this module; healthcare's
StructuredLLMClient is reused as the DeepSeek adapter but is itself
unmodified.
"""

from __future__ import annotations

import logging
from typing import Protocol, Type, TypeVar

from pydantic import BaseModel

import src.config as config

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class EnrichmentProviderError(Exception):
    """Provider call failed (timeout, transport, empty response)."""


class EnrichmentOutputError(Exception):
    """Provider responded but output failed validation after repair."""


class EnrichmentProvider(Protocol):
    """Structured-output LLM provider for enrichment features."""

    name: str
    model: str

    async def call(
        self,
        messages: list[dict],
        output_schema: Type[T],
        max_tokens: int = 800,
        temperature: float = 0.2,
    ) -> T: ...


class DeepSeekEnrichmentProvider:
    """Adapter over the existing StructuredLLMClient (unmodified reuse)."""

    name = "deepseek"

    def __init__(self) -> None:
        from src.llm.client import StructuredLLMClient

        self._client = StructuredLLMClient()
        self.model = self._client._model

    async def call(
        self,
        messages: list[dict],
        output_schema: Type[T],
        max_tokens: int = 800,
        temperature: float = 0.2,
    ) -> T:
        from src.llm.client import LLMCallError

        try:
            return await self._client.call(
                messages=messages,
                output_schema=output_schema,
                max_tokens=max_tokens,
                temperature=temperature,
                correlation_id="enrichment",
            )
        except LLMCallError as exc:
            # Distinguish transport vs validation for fail-closed reporting.
            message = str(exc)
            if "validation" in message.lower() or "schema" in message.lower():
                raise EnrichmentOutputError(message) from exc
            raise EnrichmentProviderError(message) from exc


_provider: EnrichmentProvider | None = None


def get_enrichment_provider() -> EnrichmentProvider:
    """Resolve the configured provider (lazy singleton).

    Raises EnrichmentProviderError for unknown providers — the pipeline
    catches it and fails closed without touching the source record.
    """
    global _provider
    if _provider is None:
        provider_name = (config.LLM_PROVIDER or "deepseek").lower()
        if provider_name == "deepseek":
            _provider = DeepSeekEnrichmentProvider()
        else:
            raise EnrichmentProviderError(
                f"Unknown LLM_PROVIDER '{provider_name}' — enrichment skipped"
            )
        logger.info(
            "[ENRICHMENT] Provider initialized: %s (model=%s)",
            _provider.name,
            _provider.model,
        )
    return _provider


def set_enrichment_provider(provider: EnrichmentProvider | None) -> None:
    """Inject a provider (tests use the mock); None resets to lazy config."""
    global _provider
    _provider = provider
