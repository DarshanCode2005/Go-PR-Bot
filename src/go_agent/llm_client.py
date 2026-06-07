"""Centralized LiteLLM completion client with tier routing and retries."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Literal, Protocol

from go_agent.config import Settings
from go_agent.cost_tracker import get_current_tracker

ModelTier = Literal["fast", "strong"]


class CompletionTransport(Protocol):
    def __call__(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
    ) -> Any: ...


_TRANSPORT: CompletionTransport | None = None


def llm_available(settings: Settings) -> bool:
    return bool(
        settings.openai_api_key
        or settings.anthropic_api_key
        or settings.groq_api_key
        or settings.xai_api_key
        or settings.gemini_api_key
    )


def _apply_llm_credentials(settings: Settings) -> None:
    """Expose configured API keys to LiteLLM via environment variables."""
    mapping = {
        "OPENAI_API_KEY": settings.openai_api_key,
        "ANTHROPIC_API_KEY": settings.anthropic_api_key,
        "GROQ_API_KEY": settings.groq_api_key,
        "XAI_API_KEY": settings.xai_api_key,
        "GEMINI_API_KEY": settings.gemini_api_key,
        "GOOGLE_API_KEY": settings.gemini_api_key,
    }
    for env_name, value in mapping.items():
        if value and not os.environ.get(env_name):
            os.environ[env_name] = value


def model_for_tier(tier: ModelTier, settings: Settings) -> str:
    return settings.model_strong if tier == "strong" else settings.model_fast


def _default_completion_transport(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
) -> Any:
    import litellm

    return litellm.completion(
        model=model,
        messages=messages,
        temperature=temperature,
    )


def set_completion_transport(transport: CompletionTransport | None) -> None:
    global _TRANSPORT
    _TRANSPORT = transport


def get_completion_transport() -> CompletionTransport:
    return _TRANSPORT or _default_completion_transport


def _is_rate_limit_error(exc: Exception) -> bool:
    try:
        import litellm
    except ImportError:
        return False
    return isinstance(exc, getattr(litellm, "RateLimitError", tuple()))


def _content_from_response(response: Any) -> str:
    if isinstance(response, str):
        return response
    try:
        return str(response.choices[0].message.content or "")
    except (AttributeError, IndexError, KeyError, TypeError):
        return str(response or "")


def _usage_from_response(response: Any) -> Any:
    if isinstance(response, str):
        return None
    if isinstance(response, dict):
        return response.get("usage")
    return getattr(response, "usage", None)


def _completion_cost(response: Any) -> float | None:
    if isinstance(response, str):
        return None
    try:
        import litellm

        value = litellm.completion_cost(completion_response=response)
    except Exception:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def complete(
    messages: list[dict[str, str]],
    *,
    tier: ModelTier = "fast",
    settings: Settings,
    temperature: float = 0,
    stage: str | None = None,
) -> str | None:
    """Complete chat messages using configured model tier and retries."""
    if not llm_available(settings):
        return None

    _apply_llm_credentials(settings)
    log = logging.getLogger("go_agent")
    model = model_for_tier(tier, settings)
    transport = get_completion_transport()
    max_retries = max(settings.llm_max_retries, 1)
    tracker = get_current_tracker()
    usage_stage = stage or tier

    for attempt in range(max_retries):
        try:
            response = transport(
                model=model,
                messages=messages,
                temperature=temperature,
            )
            content = _content_from_response(response)
            if tracker is not None:
                tracker.record(
                    usage_stage,
                    model,
                    _usage_from_response(response),
                    cost=_completion_cost(response),
                )
            return content
        except Exception as exc:
            if _is_rate_limit_error(exc):
                if attempt == max_retries - 1:
                    log.warning("LLM rate limited after %d attempts", max_retries)
                    if tracker is not None:
                        tracker.record_error(usage_stage, model)
                    return None
                delay = settings.llm_retry_base_delay * (2**attempt)
                log.warning(
                    "LLM rate limited; retrying in %.1fs (%d/%d)",
                    delay,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(delay)
                continue
            log.warning("LLM completion failed: %s", exc)
            if tracker is not None:
                tracker.record_error(usage_stage, model)
            return None

    return None
