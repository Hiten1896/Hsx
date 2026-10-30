from __future__ import annotations

import queue
import threading


class ChatStreamRegistry:
    """Thread-safe event queues for streaming chat responses."""

    def __init__(self):
        self._streams: dict[str, queue.Queue] = {}
        self._lock = threading.Lock()

    def register(self, job_id: str) -> queue.Queue:
        event_queue = queue.Queue()
        with self._lock:
            self._streams[job_id] = event_queue
        return event_queue

    def emit(self, job_id: str, event: str, payload: dict) -> None:
        with self._lock:
            event_queue = self._streams.get(job_id)
        if event_queue is not None:
            event_queue.put((event, payload))

    def remove(self, job_id: str) -> None:
        with self._lock:
            self._streams.pop(job_id, None)
