from __future__ import annotations

import queue
import threading
from collections import OrderedDict, deque
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChatStreamEvent:
    id: int
    event: str
    data: dict


@dataclass
class _ChatStream:
    history: deque[ChatStreamEvent] = field(default_factory=lambda: deque(maxlen=4096))
    subscribers: set[queue.Queue[ChatStreamEvent]] = field(default_factory=set)
    next_event_id: int = 1
    finished: bool = False


class ChatStreamRegistry:
    """Thread-safe event streams with bounded replay for reconnecting clients."""

    def __init__(self, retained_finished_streams: int = 128):
        self._streams: OrderedDict[str, _ChatStream] = OrderedDict()
        self._retained_finished_streams = retained_finished_streams
        self._lock = threading.Lock()

    def register(self, job_id: str) -> queue.Queue[ChatStreamEvent]:
        event_queue: queue.Queue[ChatStreamEvent] = queue.Queue()
        with self._lock:
            stream = self._streams.setdefault(job_id, _ChatStream())
            stream.subscribers.add(event_queue)
            self._streams.move_to_end(job_id)
        return event_queue

    def subscribe(
        self,
        job_id: str,
        after_event_id: int,
    ) -> tuple[queue.Queue[ChatStreamEvent] | None, list[ChatStreamEvent], bool]:
        with self._lock:
            stream = self._streams.get(job_id)
            if stream is None:
                return None, [], False
            event_queue: queue.Queue[ChatStreamEvent] = queue.Queue()
            stream.subscribers.add(event_queue)
            self._streams.move_to_end(job_id)
            replay = [event for event in stream.history if event.id > after_event_id]
            return event_queue, replay, stream.finished

    def emit(self, job_id: str, event: str, payload: dict) -> None:
        with self._lock:
            stream = self._streams.get(job_id)
            if stream is None:
                return
            item = ChatStreamEvent(stream.next_event_id, event, payload)
            stream.next_event_id += 1
            stream.history.append(item)
            stream.finished = stream.finished or event == "done"
            for event_queue in stream.subscribers:
                event_queue.put(item)
            self._prune_finished_streams()

    def remove(self, job_id: str, event_queue: queue.Queue[ChatStreamEvent] | None = None) -> None:
        with self._lock:
            stream = self._streams.get(job_id)
            if stream is None:
                return
            if event_queue is None:
                stream.subscribers.clear()
            else:
                stream.subscribers.discard(event_queue)
            self._prune_finished_streams()

    def _prune_finished_streams(self) -> None:
        finished = [
            job_id
            for job_id, stream in self._streams.items()
            if stream.finished and not stream.subscribers
        ]
        while len(finished) > self._retained_finished_streams:
            self._streams.pop(finished.pop(0), None)
