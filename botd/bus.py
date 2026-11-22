"""In-process event bus: live log lines and status changes, fanned out to UI websockets."""

import asyncio
import logging
from collections import deque

from .redact import RedactingFormatter


class EventBus:
    def __init__(self, backlog: int = 500):
        self.loop: asyncio.AbstractEventLoop | None = None
        self._subs: set[asyncio.Queue] = set()
        self.backlog: deque[dict] = deque(maxlen=backlog)

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def publish(self, message: dict) -> None:
        if message.get("type") == "log":
            self.backlog.append(message)
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._fanout, message)
        except RuntimeError:
            pass  # loop shutting down

    def _fanout(self, message: dict) -> None:
        for queue in list(self._subs):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subs.discard(queue)


bus = EventBus()


class BusLogHandler(logging.Handler):
    def __init__(self, target: EventBus):
        super().__init__()
        self.target = target
        self.setFormatter(RedactingFormatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.target.publish(
                {
                    "type": "log",
                    "ts": record.created,
                    "level": record.levelname,
                    "name": record.name,
                    "msg": self.format(record),
                }
            )
        except Exception:  # never let logging crash the app
            self.handleError(record)
