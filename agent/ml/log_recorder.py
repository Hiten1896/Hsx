from __future__ import annotations

import json
import logging
import math
import os
import queue
import re
import sys
import threading
import time
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path
from typing import Any

from config import DATA_DIR

LOGGER = logging.getLogger("hsx.ml.log_recorder")
MAX_LOG_BYTES = 8 * 1024 * 1024
MAX_MESSAGE_CHARS = 4000
MAX_EVENT_BYTES = 16 * 1024
MAX_EVENT_ITEMS = 64
MAX_BATCH_ITEMS = 64
BATCH_WINDOW_SECONDS = 0.2
SECRET_KEY = re.compile(
    r"(?i)(?:api[_ -]?(?:key|token)|access[_ -]?token|authorization|password|passwd|"
    r"private[_ -]?key|secret|cookie|credential|\btoken\b)"
)
ASSIGNMENT_SECRET = re.compile(
    r"(?i)(\b[\w.-]*(?:api[_ -]?key|token|password|secret|authorization)\b"
    r"[\"']?\s*[:=]\s*[\"']?)([^\s,;\"'}]+)"
)
BEARER_SECRET = re.compile(r"(?i)(\bbearer\s+)([A-Za-z0-9._~+/=-]{8,})")
KNOWN_SECRETS = tuple(
    value
    for key, value in os.environ.items()
    if value and len(value) >= 8
    and any(marker in key.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
)


def _redact_text(value: str) -> str:
    value = BEARER_SECRET.sub(r"\1[REDACTED]", value)
    value = ASSIGNMENT_SECRET.sub(r"\1[REDACTED]", value)
    for secret in KNOWN_SECRETS:
        value = value.replace(secret, "[REDACTED]")
    return value[:MAX_MESSAGE_CHARS]


def _safe_value(value: Any, key: str = "", depth: int = 0) -> Any:
    if SECRET_KEY.search(key):
        return "[REDACTED]"
    if depth >= 5:
        return "[DEPTH_LIMIT]"
    if isinstance(value, str):
        return _redact_text(value)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Path):
        return str(value)[:MAX_MESSAGE_CHARS]
    if isinstance(value, dict):
        return {
            str(child_key)[:120]: _safe_value(child, str(child_key), depth + 1)
            for child_key, child in islice(value.items(), MAX_EVENT_ITEMS)
            if not any(marker in str(child_key).lower() for marker in ("image", "audio", "base64"))
        }
    if isinstance(value, (list, tuple, set)):
        return [
            _safe_value(child, depth=depth + 1)
            for child in islice(value, MAX_EVENT_ITEMS)
        ]
    return str(value)[:MAX_MESSAGE_CHARS]


class _RecordHandler(logging.Handler):
    def __init__(self, recorder: "LogRecorder"):
        super().__init__(logging.INFO)
        self.recorder = recorder

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info:
                formatter = self.formatter or logging.Formatter()
                message += "\n" + formatter.formatException(record.exc_info)
            self.recorder._enqueue({
                "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
                "record_type": "python_log",
                "logger": record.name[:160],
                "level": record.levelname[:20],
                "message": _redact_text(message),
            })
        except (TypeError, ValueError):
            self.handleError(record)


class LogRecorder:
    """Batch bounded JSONL telemetry from Python logging and explicit runtime hooks."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        max_bytes: int = MAX_LOG_BYTES,
        queue_size: int = 2048,
    ):
        if max_bytes < 16 * 1024:
            raise ValueError("max_bytes must be at least 16 KiB.")
        if queue_size < 1:
            raise ValueError("queue_size must be positive.")
        self.path = path or DATA_DIR / "datasets" / "agent_logs.jsonl"
        self.max_bytes = max_bytes
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=queue_size)
        self._handler = _RecordHandler(self)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._installed = False
        self._closed = False
        self._dropped = 0
        self._previous_root_level: int | None = None

    @property
    def is_installed(self) -> bool:
        return self._installed

    @property
    def dropped_records(self) -> int:
        return self._dropped

    def install(self) -> bool:
        with self._lock:
            if self._closed:
                raise RuntimeError("The log recorder has been stopped.")
            if self._installed:
                return False
            self._start_worker()
            root = logging.getLogger()
            if root.level > logging.INFO:
                self._previous_root_level = root.level
                root.setLevel(logging.INFO)
            root.addHandler(self._handler)
            self._installed = True
            return True

    def record_event(
        self,
        event: str,
        payload: dict[str, Any] | None = None,
        *,
        sensitive: bool = False,
    ) -> bool:
        if not isinstance(event, str) or not event.strip():
            raise ValueError("event must not be empty.")
        safe_payload = {"redacted": True} if sensitive else _safe_value(payload or {})
        if len(json.dumps(safe_payload, ensure_ascii=False).encode("utf-8")) > MAX_EVENT_BYTES:
            safe_payload = {"truncated": True}
        return self._enqueue({
            "timestamp": datetime.now(UTC).isoformat(),
            "record_type": "runtime_event",
            "event": event[:120],
            "payload": safe_payload,
        })

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._installed:
                root = logging.getLogger()
                root.removeHandler(self._handler)
                if self._previous_root_level is not None and root.level == logging.INFO:
                    root.setLevel(self._previous_root_level)
                self._installed = False
            thread = self._thread
        if thread is None:
            return
        try:
            self._queue.put(None, timeout=max(0.1, timeout))
        except queue.Full:
            sys.stderr.write("HSX log recorder queue is full; shutdown flush may be incomplete.\n")
        thread.join(timeout)
        if thread.is_alive():
            sys.stderr.write("HSX log recorder did not stop before its timeout.\n")
        if self._dropped:
            sys.stderr.write(f"HSX log recorder dropped {self._dropped} queued records.\n")

    def _start_worker(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._write_loop,
            name="hsx-log-recorder",
            daemon=True,
        )
        self._thread.start()

    def _enqueue(self, record: dict[str, Any]) -> bool:
        with self._lock:
            if self._closed:
                return False
            self._start_worker()
            try:
                self._queue.put_nowait(record)
                return True
            except queue.Full:
                self._dropped += 1
                return False

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        if not self.path.exists() or self.path.stat().st_size + incoming_bytes <= self.max_bytes:
            return
        backup = self.path.with_name(f"{self.path.stem}.1{self.path.suffix}")
        backup.unlink(missing_ok=True)
        os.replace(self.path, backup)

    def _write_batch(self, batch: list[dict[str, Any]]) -> None:
        if not batch:
            return
        encoded_lines = [
            (json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
            for item in batch
        ]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        current_size = self.path.stat().st_size if self.path.exists() else 0
        pending = bytearray()
        for line in encoded_lines:
            if len(line) > self.max_bytes:
                raise ValueError("A log record exceeds the configured rotation limit.")
            if current_size + len(pending) + len(line) > self.max_bytes:
                self._append_bytes(pending)
                current_size += len(pending)
                pending.clear()
                self._rotate_if_needed(len(line))
                current_size = self.path.stat().st_size if self.path.exists() else 0
            pending.extend(line)
        self._append_bytes(pending)

    def _append_bytes(self, data: bytearray) -> None:
        if not data:
            return
        descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            remaining = memoryview(data)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("Could not append the dataset log batch.")
                remaining = remaining[written:]
        finally:
            os.close(descriptor)

    def _write_loop(self) -> None:
        stopping = False
        while not stopping:
            try:
                first = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if first is None:
                self._queue.task_done()
                return
            batch = [first]
            queued_count = 1
            sentinel_queued = False
            deadline = time.monotonic() + BATCH_WINDOW_SECONDS
            while len(batch) < MAX_BATCH_ITEMS:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    record = self._queue.get(timeout=remaining)
                except queue.Empty:
                    break
                if record is None:
                    stopping = True
                    sentinel_queued = True
                    break
                queued_count += 1
                batch.append(record)
            try:
                self._write_batch(batch)
            except (OSError, TypeError, ValueError) as error:
                sys.stderr.write(f"HSX log recorder could not persist a batch: {error}\n")
            finally:
                for _ in range(queued_count):
                    self._queue.task_done()
            if sentinel_queued:
                self._queue.task_done()


LOG_RECORDER = LogRecorder()
