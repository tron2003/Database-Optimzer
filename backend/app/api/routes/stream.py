"""Server-sent events for the live dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from sse_starlette.sse import EventSourceResponse

from app.collector.broadcast import broadcaster

router = APIRouter(tags=["stream"])


@router.get("/stream")
async def stream(request: Request, connection_id: str | None = Query(default=None)):
    """Live metrics.

    SSE rather than a WebSocket: the traffic is one-directional, it survives
    proxies that mangle upgrade headers, and the browser reconnects on its own.
    """

    async def publisher():
        async for event in broadcaster.stream(connection_id):
            if await request.is_disconnected():
                break
            yield event

    return EventSourceResponse(
        publisher(),
        headers={
            "Cache-Control": "no-cache",
            # Stops nginx buffering the stream into uselessness.
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/stream/stats")
async def stream_stats():
    return {
        "subscribers": broadcaster.subscriber_count,
        "dropped_events": broadcaster.dropped_events,
        "note": (
            "Events are dropped for a subscriber whose queue is full — a tab that "
            "stopped reading. The collector is never blocked by a slow client."
        ),
    }
