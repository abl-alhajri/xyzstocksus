"""Server-Sent Events broadcaster.

Used by the dashboard's mobile UI to receive live updates on signals + BTC
price + scan progress. The orchestrator pushes events here; clients subscribe
via /api/stream.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from typing import Iterator

_LOCK = threading.Lock()
_SUBSCRIBERS: list[queue.Queue] = []
_KEEPALIVE_SECONDS = 25


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=200)
    with _LOCK:
        _SUBSCRIBERS.append(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _LOCK:
        if q in _SUBSCRIBERS:
            _SUBSCRIBERS.remove(q)


def publish(event: str, data: dict) -> None:
    payload = {"event": event, "data": data, "ts": time.time()}
    msg = f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
    with _LOCK:
        for q in list(_SUBSCRIBERS):
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass


def stream(client_q: queue.Queue) -> Iterator[str]:
    """Yield SSE chunks for a single subscriber. Includes periodic keepalive."""
    yield "event: hello\ndata: {\"ok\": true}\n\n"
    last_ka = time.time()
    while True:
        try:
            msg = client_q.get(timeout=5)
            yield msg
        except queue.Empty:
            now = time.time()
            if now - last_ka > _KEEPALIVE_SECONDS:
                yield ": keepalive\n\n"
                last_ka = now
