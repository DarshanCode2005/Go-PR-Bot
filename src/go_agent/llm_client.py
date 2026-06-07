"""Centralized LiteLLM completion client with tier routing and retries."""

from __future__ import annotations

import logging
import time
from typing import Literal, Protocol

from go_agent.config import Settings

ModelTier = Literal["fast", "strong"]


class CompletionTransport(Protocol):
    def __call__(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
    ) -> str: ...


_TRANSPORT: CompletionTransport | None = None


def llm_available(settings: Settings) -> bool:
    return bool(
        settings.openai_api_key
        or settings.anthropic_api_key
        or settings.groq_api_key
        or settings.xai_api_key
    )


def model_for_tier(tier: ModelTier, settings: Settings) -> str:
    return settings.model_strong if tier == "strong" else settings.model_fast


def _default_completion_transport(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
) -> str:
    import litellm

    response = litellm.completion(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    return str(response.choices[0].message.content or "")


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


def complete(
    messages: list[dict[str, str]],
    *,
    tier: ModelTier = "fast",
    settings: Settings,
    temperature: float = 0,
) -> str | None:
    """Complete chat messages using configured model tier and retries."""
    if not llm_available(settings):
        return None

    log = logging.getLogger("go_agent")
    model = model_for_tier(tier, settings)
    transport = get_completion_transport()
    max_retries = max(settings.llm_max_retries, 1)

    for attempt in range(max_retries):
        try:
            content = transport(
                model=model,
                messages=messages,
                temperature=temperature,
            )
            return content
        except Exception as exc:
            if _is_rate_limit_error(exc):
                if attempt == max_retries - 1:
                    log.warning("LLM rate limited after %d attempts", max_retries)
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
            return None

    return None
