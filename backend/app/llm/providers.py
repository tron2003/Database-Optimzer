"""Concrete providers: Ollama, Anthropic, OpenAI-compatible.

All three are reached over HTTP with ``httpx`` rather than through vendor
SDKs. The surface used here is small and stable, and it keeps the dependency
list short: an install that only ever talks to a local Ollama should not need
two cloud SDKs on disk.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings
from app.llm.base import LLMError, LLMProvider, Message

log = logging.getLogger(__name__)


def _messages_payload(messages: list[Message]) -> list[dict[str, str]]:
    return [{"role": m.role, "content": m.content} for m in messages]


class OllamaProvider(LLMProvider):
    """Local models through Ollama's /api/chat endpoint.

    The default for this tool. Schema text and query text stay on the machine,
    which matters because both routinely contain things an organisation would
    not choose to send to a third party.
    """

    name = "ollama"

    def __init__(self, settings: Settings) -> None:
        self._model = settings.ollama_model
        self._max_tokens = settings.llm_max_output_tokens
        self._client = httpx.AsyncClient(
            base_url=settings.ollama_base_url.rstrip("/"),
            timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=10.0),
        )

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        max_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "stream": False,
            "messages": [{"role": "system", "content": system}, *_messages_payload(messages)],
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens or self._max_tokens,
            },
        }
        try:
            response = await self._client.post("/api/chat", json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:500]
            if exc.response.status_code == 404:
                raise LLMError(
                    f"Ollama does not have the model {self._model!r}. "
                    f"Pull it first: ollama pull {self._model}"
                ) from exc
            raise LLMError(f"Ollama returned {exc.response.status_code}: {body}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(
                f"Could not reach Ollama at {self._client.base_url}: {exc}. "
                f"Is it running? (docker compose --profile ollama up -d)"
            ) from exc

        data = response.json()
        content = (data.get("message") or {}).get("content", "")
        if not content:
            raise LLMError("Ollama returned an empty completion")
        return content

    async def aclose(self) -> None:
        await self._client.aclose()


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, settings: Settings) -> None:
        if not settings.anthropic_api_key:
            raise LLMError("DBOPT_ANTHROPIC_API_KEY is not set")
        self._model = settings.anthropic_model
        self._max_tokens = settings.llm_max_output_tokens
        self._client = httpx.AsyncClient(
            base_url="https://api.anthropic.com",
            timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=10.0),
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        max_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> str:
        payload = {
            "model": self._model,
            "max_tokens": max_tokens or self._max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": _messages_payload(messages),
        }
        try:
            response = await self._client.post("/v1/messages", json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                f"Anthropic API returned {exc.response.status_code}: {exc.response.text[:500]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Could not reach the Anthropic API: {exc}") from exc

        data = response.json()
        parts = [block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"]
        text = "".join(parts)
        if not text:
            raise LLMError("Anthropic API returned no text content")
        return text

    async def aclose(self) -> None:
        await self._client.aclose()


class OpenAIProvider(LLMProvider):
    """Any OpenAI-compatible chat-completions endpoint.

    Works unchanged against OpenAI, vLLM, LM Studio, llama.cpp's server and
    similar, by pointing DBOPT_OPENAI_BASE_URL at them.
    """

    name = "openai"

    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise LLMError("DBOPT_OPENAI_API_KEY is not set")
        self._model = settings.openai_model
        self._max_tokens = settings.llm_max_output_tokens
        self._client = httpx.AsyncClient(
            base_url=settings.openai_base_url.rstrip("/"),
            timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=10.0),
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "content-type": "application/json",
            },
        )

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        max_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> str:
        payload = {
            "model": self._model,
            "max_tokens": max_tokens or self._max_tokens,
            "temperature": temperature,
            "messages": [{"role": "system", "content": system}, *_messages_payload(messages)],
        }
        try:
            response = await self._client.post("/chat/completions", json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                f"OpenAI-compatible endpoint returned {exc.response.status_code}: "
                f"{exc.response.text[:500]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Could not reach the OpenAI-compatible endpoint: {exc}") from exc

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("OpenAI-compatible endpoint returned no choices")
        content = (choices[0].get("message") or {}).get("content", "")
        if not content:
            raise LLMError("OpenAI-compatible endpoint returned an empty completion")
        return content

    async def aclose(self) -> None:
        await self._client.aclose()
