"""Tests for centralized LiteLLM completion client."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import pytest

from go_agent.config import Settings, clear_settings_cache
from go_agent.llm_client import complete, get_completion_transport, set_completion_transport


class RecordingTransport:
    def __init__(self, responses: list[str | Exception]):
        self.responses = responses
        self.calls: list[dict[str, Any]] = []
        self.index = 0

    def __call__(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
    ) -> str:
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
        )
        result = self.responses[min(self.index, len(self.responses) - 1)]
        self.index += 1
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture(autouse=True)
def _reset_state() -> Callable[[], None]:
    clear_settings_cache()
    set_completion_transport(None)
    yield
    set_completion_transport(None)
    clear_settings_cache()


def test_complete_uses_fast_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = Settings()
    transport = RecordingTransport(["ok"])
    set_completion_transport(transport)

    out = complete([{"role": "user", "content": "hello"}], tier="fast", settings=settings)

    assert out == "ok"
    assert transport.calls[0]["model"] == settings.model_fast


def test_complete_uses_strong_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = Settings()
    transport = RecordingTransport(["ok"])
    set_completion_transport(transport)

    out = complete([{"role": "user", "content": "hello"}], tier="strong", settings=settings)

    assert out == "ok"
    assert transport.calls[0]["model"] == settings.model_strong


def test_complete_returns_none_without_api_keys():
    settings = Settings(openai_api_key=None, anthropic_api_key=None)
    transport = RecordingTransport(["ok"])
    set_completion_transport(transport)

    out = complete([{"role": "user", "content": "hello"}], settings=settings)

    assert out is None
    assert not transport.calls


def test_complete_retries_on_rate_limit(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = Settings(llm_max_retries=3, llm_retry_base_delay=0.01)
    transport = RecordingTransport([RuntimeError("rate"), RuntimeError("rate"), "ok"])
    set_completion_transport(transport)

    with (
        patch("go_agent.llm_client._is_rate_limit_error", return_value=True),
        patch("go_agent.llm_client.time.sleep"),
    ):
        out = complete([{"role": "user", "content": "hello"}], settings=settings)

    assert out == "ok"
    assert len(transport.calls) == 3


def test_complete_returns_none_after_exhausted_retries(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = Settings(llm_max_retries=2, llm_retry_base_delay=0.01)
    transport = RecordingTransport([RuntimeError("rate"), RuntimeError("rate")])
    set_completion_transport(transport)

    with (
        patch("go_agent.llm_client._is_rate_limit_error", return_value=True),
        patch("go_agent.llm_client.time.sleep"),
    ):
        out = complete([{"role": "user", "content": "hello"}], settings=settings)

    assert out is None
    assert len(transport.calls) == 2


def test_set_completion_transport_injects_mock():
    transport = RecordingTransport(["ok"])
    set_completion_transport(transport)
    assert get_completion_transport() is transport
