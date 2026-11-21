"""Server-sent event bus for live notifications.

A tiny thread-safe fan-out: the proactive loop publishes an event from its own
thread; each browser holding an /api/events stream reads it from its own queue.
No third-party package, no external push service, no credentials -- which is
what keeps this course demo runnable offline and verifiable in class.

Scope (honest): notifications are delivered while the page is open, because
that is when the browser holds the event stream. True background push after
the tab is closed needs Web Push (VAPID + FCM); see docs/APP.md.
"""
from __future__ import annotations

import json
import queue
import threading
from typing import Any


class EventBus:
    def __init__(self, max_queue: int = 64) -> None:
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._max = max_queue
        self._recent: list[dict] = []  # last events, for late subscribers

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self._max)
        with self._lock:
            self._subs.append(q)
            snapshot = list(self._recent[-8:])
        for e in snapshot:
            try:
                q.put_nowait(e)
            except queue.Full:
                break
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        with self._lock:
            self._recent.append(event)
            self._recent = self._recent[-8:]
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                # A slow/stuck browser must never block the safety loop.
                pass


_bus = EventBus()


def emit(kind: str, title: str, body: str = "", **extra: Any) -> None:
    """Publish a notification. Never raises: notifications are optional."""
    try:
        _bus.publish({"kind": kind, "title": title, "body": body, **extra})
    except Exception:
        pass


def subscribe() -> queue.Queue:
    return _bus.subscribe()


def unsubscribe(q: queue.Queue) -> None:
    _bus.unsubscribe(q)


def sse(event: dict) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False, default=str) + "\n\n"
