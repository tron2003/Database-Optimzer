"""In-process fan-out of collector events to SSE subscribers.

One queue per subscriber, bounded. A browser tab that stops reading (laptop
lid closed, tab backgrounded) must not grow a queue without limit, so a full
queue drops its oldest event rather than blocking the collector. Losing a
metrics tick for a slow reader is correct behaviour; stalling the collector
for everyone is not.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

MAX_QUEUE = 100


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)


@dataclass(slots=True)
class Event:
    type: str
    connection_id: str
    payload: dict[str, Any]
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_sse(self) -> dict[str, str]:
        return {
            "event": self.type,
            "data": json.dumps(
                {
                    "connection_id": self.connection_id,
                    "at": self.at.isoformat(),
                    **self.payload,
                },
                default=_json_default,
            ),
        }


class Broadcaster:
    def __init__(self) -> None:
        self._subscribers: dict[int, tuple[asyncio.Queue[Event], str | None]] = {}
        self._next_id = 0
        self._lock = asyncio.Lock()
        self._dropped = 0

    async def publish(self, event: Event) -> None:
        async with self._lock:
            targets = [
                queue
                for queue, conn_filter in self._subscribers.values()
                if conn_filter is None or conn_filter == event.connection_id
            ]
        for queue in targets:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self._dropped += 1
                try:
                    queue.get_nowait()       # discard oldest
                    queue.put_nowait(event)  # keep newest
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    async def subscribe(
        self, connection_id: str | None = None
    ) -> tuple[int, asyncio.Queue[Event]]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=MAX_QUEUE)
        async with self._lock:
            self._next_id += 1
            sub_id = self._next_id
            self._subscribers[sub_id] = (queue, connection_id)
        return sub_id, queue

    async def unsubscribe(self, sub_id: int) -> None:
        async with self._lock:
            self._subscribers.pop(sub_id, None)

    async def stream(
        self, connection_id: str | None = None, *, heartbeat: float = 15.0
    ) -> AsyncIterator[dict[str, str]]:
        """Yield SSE dicts until the client disconnects.

        The heartbeat comment keeps intermediaries from closing an idle
        connection, which otherwise makes a quiet database look like a broken
        dashboard.
        """
        sub_id, queue = await self.subscribe(connection_id)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=heartbeat)
                except TimeoutError:
                    yield {"event": "ping", "data": "{}"}
                    continue
                yield event.to_sse()
        finally:
            await self.unsubscribe(sub_id)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def dropped_events(self) -> int:
        return self._dropped


broadcaster = Broadcaster()
