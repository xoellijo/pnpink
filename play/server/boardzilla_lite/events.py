from __future__ import annotations

import asyncio
from collections import defaultdict


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[int, dict[asyncio.Queue[str], str]] = defaultdict(dict)

    def subscribe(self, session_id: int, access_token: str) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=8)
        self._subscribers[session_id][queue] = access_token
        return queue

    def unsubscribe(self, session_id: int, queue: asyncio.Queue[str]) -> None:
        subscribers = self._subscribers.get(session_id)
        if subscribers is None:
            return
        subscribers.pop(queue, None)
        if not subscribers:
            self._subscribers.pop(session_id, None)

    def publish(self, session_id: int, event: str = "refresh", access_tokens: set[str] | None = None) -> None:
        for queue, access_token in tuple(self._subscribers.get(session_id, {}).items()):
            if access_tokens is not None and access_token not in access_tokens:
                continue
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

