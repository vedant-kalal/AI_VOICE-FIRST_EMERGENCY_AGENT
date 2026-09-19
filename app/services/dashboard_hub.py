"""Live push channel: backend -> ops dashboard (PDF §11 'Live updates: WebSockets').

Tool execution runs in worker threads (asyncio.to_thread) so the audio loop never blocks on the DB.
`publish()` is therefore thread-safe: it hands the event to the server's event loop.
"""
import asyncio
import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class DashboardHub:
    def __init__(self, backlog: int = 200):
        self._clients: set[WebSocket] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.recent: deque = deque(maxlen=backlog)  # replayed to a dashboard that just connected

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        for event in list(self.recent):
            await ws.send_text(event)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def _broadcast(self, message: str) -> None:
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    def publish(self, event_type: str, data: dict) -> None:
        """Fire-and-forget from any thread or coroutine. Never raises into the caller."""
        try:
            message = json.dumps(
                {"type": event_type, "ts": datetime.now(timezone.utc).isoformat(), "data": data},
                default=str,
            )
            self.recent.append(message)
            if not self._loop or not self._clients:
                return
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is self._loop:
                self._loop.create_task(self._broadcast(message))
            else:
                asyncio.run_coroutine_threadsafe(self._broadcast(message), self._loop)
        except Exception:
            logger.exception("dashboard publish failed (event=%s)", event_type)


hub = DashboardHub()
