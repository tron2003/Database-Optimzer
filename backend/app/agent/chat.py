"""The natural-language console loop.

A bounded tool-calling loop: the model may call up to ``MAX_STEPS`` tools,
after which it must answer with whatever it has. The bound matters because
the loop runs against a live database — an agent that decides to enumerate
every table one call at a time should hit a wall, not a rate limit.

Each tool result is appended to the conversation as text. Results are
truncated: a schema dump can exceed a small local model's context on its own,
and a truncated result the model can see is more useful than a complete one
that pushes the question out of the window.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.agent.tools import ToolContext, call_tool, tool_catalog_text
from app.llm.base import LLMError, Message
from app.llm.prompts import CHAT_AGENT
from app.llm.registry import get_provider
from app.llm.schemas import ChatTurn

log = logging.getLogger(__name__)

MAX_STEPS = 6
MAX_TOOL_RESULT_CHARS = 12_000


@dataclass(slots=True)
class ChatStep:
    kind: str  # "tool_call" | "answer" | "error"
    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    text: str = ""

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind}
        if self.name:
            out["name"] = self.name
        if self.arguments:
            out["arguments"] = self.arguments
        if self.result is not None:
            out["result"] = self.result
        if self.text:
            out["text"] = self.text
        return out


def _truncate(payload: Any) -> str:
    text = json.dumps(payload, indent=2, default=str)
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return text
    return (
        text[:MAX_TOOL_RESULT_CHARS]
        + f"\n... [truncated; {len(text) - MAX_TOOL_RESULT_CHARS} more characters]"
    )


async def run_chat(
    *,
    question: str,
    ctx: ToolContext,
    history: list[Message] | None = None,
    thread_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    provider = get_provider()
    system = f"{CHAT_AGENT}\n\n## Tools\n\n{tool_catalog_text()}"

    messages: list[Message] = list(history or [])
    messages.append(Message(role="user", content=question))

    steps: list[ChatStep] = []
    answer = ""

    for step_no in range(1, MAX_STEPS + 1):
        last_step = step_no == MAX_STEPS
        if last_step:
            messages.append(
                Message(
                    role="user",
                    content=(
                        "You have reached the tool-call limit for this question. "
                        "Answer now with what you have, and say plainly what you "
                        "could not determine."
                    ),
                )
            )

        try:
            turn = await provider.complete_structured(
                system=system, messages=messages, model_cls=ChatTurn
            )
        except LLMError as exc:
            steps.append(ChatStep(kind="error", text=str(exc)))
            return {
                "thread_id": str(thread_id) if thread_id else None,
                "answer": "",
                "steps": [s.as_dict() for s in steps],
                "error": str(exc),
            }

        if turn.tool_call and not turn.done:
            name = turn.tool_call.name
            args = turn.tool_call.arguments or {}
            result = await call_tool(ctx, name, args)
            steps.append(ChatStep(kind="tool_call", name=name, arguments=args, result=result))

            messages.append(
                Message(
                    role="assistant",
                    content=json.dumps(
                        {"tool_call": {"name": name, "arguments": args}}, default=str
                    ),
                )
            )
            messages.append(
                Message(
                    role="user",
                    content=f"Result of {name}:\n```json\n{_truncate(result)}\n```",
                )
            )
            continue

        answer = (turn.answer or "").strip()
        if answer:
            steps.append(ChatStep(kind="answer", text=answer))
            break

        # Neither a tool call nor an answer. Nudge once, then give up.
        messages.append(
            Message(
                role="user",
                content="That response contained neither a tool call nor an answer. "
                "Either call a tool or answer the question.",
            )
        )

    if not answer:
        answer = (
            "I could not reach a conclusion within the tool-call limit for this "
            "question. Try asking something narrower."
        )
        steps.append(ChatStep(kind="answer", text=answer))

    return {
        "thread_id": str(thread_id) if thread_id else None,
        "answer": answer,
        "steps": [s.as_dict() for s in steps],
        "tool_calls": sum(1 for s in steps if s.kind == "tool_call"),
    }
