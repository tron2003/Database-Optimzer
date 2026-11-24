"""Natural-language console."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.chat import run_chat
from app.agent.tools import ToolContext, tool_catalog_text
from app.api.deps import ResolvedConnection, get_target
from app.config import get_settings
from app.db.meta import get_session
from app.db.models import ChatMessage
from app.llm.base import Message
from app.schemas.api import ChatRequest, ChatResponse

router = APIRouter(prefix="/connections/{connection_id}", tags=["chat"])

HISTORY_TURNS = 8


@router.get("/chat/tools")
async def list_tools():
    """What the console can actually look at.

    Exposed so the UI can show it. There is deliberately no SQL-execution
    tool, and a user should be able to confirm that for themselves.
    """
    return {
        "tools": tool_catalog_text(),
        "note": (
            "The console can only call these tools. It cannot run arbitrary SQL and "
            "cannot modify anything; recommendations go to the suggestions queue for "
            "approval."
        ),
    }


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    target: ResolvedConnection = Depends(get_target),
    session: AsyncSession = Depends(get_session),
):
    settings = get_settings()
    if not settings.llm_enabled:
        raise HTTPException(
            status_code=503,
            detail=(
                "No LLM provider is configured, so the console is unavailable. Set "
                "DBOPT_LLM_PROVIDER to ollama, anthropic or openai. The dashboard, "
                "slow-query analysis, index advice and EXPLAIN all work without one."
            ),
        )

    thread_id = payload.thread_id or uuid.uuid4()

    history: list[Message] = []
    if payload.thread_id:
        rows = (
            await session.execute(
                select(ChatMessage)
                .where(ChatMessage.thread_id == thread_id)
                .order_by(ChatMessage.created_at.desc())
                .limit(HISTORY_TURNS * 2)
            )
        ).scalars().all()
        for row in reversed(rows):
            if row.role in ("user", "assistant"):
                history.append(Message(role=row.role, content=row.content))

    ctx = ToolContext(
        pool=target.pool,
        allow_explain_analyze=target.row.allow_explain_analyze,
        has_pg_stat_statements=target.has_pg_stat_statements,
    )

    result = await run_chat(
        question=payload.question, ctx=ctx, history=history, thread_id=thread_id
    )

    session.add(
        ChatMessage(
            thread_id=thread_id,
            connection_id=target.row.id,
            role="user",
            content=payload.question,
        )
    )
    session.add(
        ChatMessage(
            thread_id=thread_id,
            connection_id=target.row.id,
            role="assistant",
            content=result.get("answer") or "",
            tool_calls=[s for s in result.get("steps", []) if s.get("kind") == "tool_call"],
        )
    )
    await session.commit()

    return ChatResponse(**{**result, "thread_id": str(thread_id)})


@router.get("/chat/{thread_id}")
async def get_thread(
    thread_id: uuid.UUID,
    target: ResolvedConnection = Depends(get_target),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(ChatMessage)
            .where(
                ChatMessage.thread_id == thread_id,
                ChatMessage.connection_id == target.row.id,
            )
            .order_by(ChatMessage.created_at)
        )
    ).scalars().all()
    return {
        "thread_id": str(thread_id),
        "messages": [
            {
                "role": r.role,
                "content": r.content,
                "tool_calls": r.tool_calls,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }
