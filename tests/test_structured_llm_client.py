"""Unit tests for StructuredLLMClient resilience.

DeepSeek intermittently returns an EMPTY / whitespace-only completion in JSON
mode (it comes and goes under load). One empty turn would drop a live caller, so
``call()`` retries in JSON mode and then falls back to a non-JSON completion
(``_try_parse`` still extracts the JSON object) before giving up.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from src.llm.client import LLMCallError, StructuredLLMClient


class _Out(BaseModel):
    value: str


@pytest.fixture
def client(monkeypatch):
    # AsyncOpenAI rejects an empty api_key; give it a dummy (we mock _raw_call).
    monkeypatch.setattr("src.llm.client.DEEPSEEK_API_KEY", "sk-test")
    return StructuredLLMClient()


def test_call_recovers_when_json_mode_returns_empty_then_falls_back(client):
    # Empty in JSON mode, empty on the JSON retry, then valid JSON without JSON mode.
    client._raw_call = AsyncMock(side_effect=["", "", json.dumps({"value": "ok"})])
    result = asyncio.run(client.call([{"role": "user", "content": "hi"}], _Out))
    assert result.value == "ok"
    assert client._raw_call.await_count == 3  # json, json-retry, non-json fallback


def test_call_succeeds_on_second_attempt_without_fallback(client):
    client._raw_call = AsyncMock(side_effect=["", json.dumps({"value": "ok"})])
    result = asyncio.run(client.call([{"role": "user", "content": "hi"}], _Out))
    assert result.value == "ok"
    assert client._raw_call.await_count == 2  # recovered on the JSON-mode retry


def test_call_raises_only_after_all_retries_exhausted(client):
    client._raw_call = AsyncMock(return_value="")
    with pytest.raises(LLMCallError):
        asyncio.run(client.call([{"role": "user", "content": "hi"}], _Out))
    assert client._raw_call.await_count == 3
