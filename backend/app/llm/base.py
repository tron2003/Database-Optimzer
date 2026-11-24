"""Provider-agnostic LLM interface.

One narrow surface — ``complete`` for prose, ``complete_structured`` for a
validated Pydantic model — so that swapping a local model for a hosted one is
a configuration change rather than a code change.

``complete_structured`` owns the retry loop. Small local models in particular
will return JSON wrapped in prose or fenced in markdown, and will occasionally
miss a required field; one corrective round trip fixes most of it. After the
retries are spent the call raises rather than returning something
half-understood, because the caller's next step is to render SQL from these
fields.
"""

from __future__ import annotations

import abc
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class LLMError(RuntimeError):
    """The provider failed, timed out, or returned something unusable."""


class LLMDisabled(LLMError):
    """An LLM feature was invoked while the provider is set to 'none'."""


@dataclass(slots=True)
class Message:
    role: str  # "user" | "assistant"
    content: str


class LLMProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        max_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> str:
        """Return a plain-text completion."""

    @abc.abstractmethod
    async def aclose(self) -> None:
        """Release any held client resources."""

    async def complete_structured(
        self,
        *,
        system: str,
        messages: list[Message],
        model_cls: type[T],
        max_tokens: int | None = None,
        attempts: int = 3,
    ) -> T:
        schema = json.dumps(model_cls.model_json_schema(), indent=2)
        instruction = (
            f"{system}\n\n"
            "Reply with a single JSON object and nothing else. No prose before or "
            "after it, no markdown fence. It must validate against this JSON Schema:\n\n"
            f"{schema}\n\n"
            "Omit optional fields you have no basis for rather than inventing a value. "
            "If the evidence does not support a confident answer, say so in the "
            "designated field instead of guessing."
        )

        convo = list(messages)
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            raw = await self.complete(
                system=instruction,
                messages=convo,
                max_tokens=max_tokens,
                temperature=0.0 if attempt > 1 else 0.2,
            )
            try:
                return model_cls.model_validate(_extract_json(raw))
            except (ValueError, ValidationError) as exc:
                last_error = exc
                log.warning(
                    "%s returned unusable structured output (attempt %d/%d): %s",
                    self.name,
                    attempt,
                    attempts,
                    exc,
                )
                if attempt == attempts:
                    break
                convo = convo + [
                    Message(role="assistant", content=raw[:4000]),
                    Message(
                        role="user",
                        content=(
                            "That did not validate against the schema. The error was:\n"
                            f"{exc}\n\n"
                            "Return only the corrected JSON object."
                        ),
                    ),
                ]

        raise LLMError(
            f"{self.name} did not return schema-valid JSON after {attempts} attempts: "
            f"{last_error}"
        )


def _extract_json(raw: str) -> Any:
    """Pull a JSON object out of a completion that may be wrapped."""
    if not raw or not raw.strip():
        raise ValueError("empty completion")

    text = raw.strip()

    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fall back to the outermost balanced object, which handles a model that
    # prefixed its answer with a sentence of explanation.
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found in completion")

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])

    raise ValueError("unterminated JSON object in completion")


class NullProvider(LLMProvider):
    """Used when DBOPT_LLM_PROVIDER=none.

    Every LLM entry point goes through the registry, so this keeps the
    disabled case a clean, explicit error at the boundary rather than a
    None-check scattered through the call sites. The deterministic analysis
    continues to work with this installed.
    """

    name = "none"

    async def complete(self, **_: Any) -> str:
        raise LLMDisabled(
            "No LLM provider is configured. Set DBOPT_LLM_PROVIDER to ollama, "
            "anthropic or openai to enable this feature. All deterministic analysis "
            "works without one."
        )

    async def aclose(self) -> None:
        return None
