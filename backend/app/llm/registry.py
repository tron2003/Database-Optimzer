"""Provider selection and lifecycle."""

from __future__ import annotations

import logging

from app.config import get_settings
from app.llm.base import LLMError, LLMProvider, NullProvider
from app.llm.providers import AnthropicProvider, OllamaProvider, OpenAIProvider

log = logging.getLogger(__name__)

_provider: LLMProvider | None = None


def get_provider() -> LLMProvider:
    """Return the configured provider, constructing it on first use.

    A misconfigured provider (missing key, unreachable host) degrades to the
    null provider rather than taking down the application. Every LLM feature
    is optional; the deterministic analysis is not, and must keep working.
    """
    global _provider
    if _provider is not None:
        return _provider

    settings = get_settings()
    try:
        if settings.llm_provider == "ollama":
            _provider = OllamaProvider(settings)
        elif settings.llm_provider == "anthropic":
            _provider = AnthropicProvider(settings)
        elif settings.llm_provider == "openai":
            _provider = OpenAIProvider(settings)
        else:
            _provider = NullProvider()
    except LLMError as exc:
        log.warning("LLM provider %s unavailable: %s", settings.llm_provider, exc)
        _provider = NullProvider()

    log.info("LLM provider: %s", _provider.name)
    return _provider


def provider_is_remote() -> bool:
    """True when prompts leave this machine.

    Used to decide whether redaction is applied. A local Ollama does not need
    it; a hosted API does.
    """
    return get_provider().name in {"anthropic", "openai"}


async def close_provider() -> None:
    global _provider
    if _provider is not None:
        await _provider.aclose()
    _provider = None
