from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import DATA_DIR

LOGGER = logging.getLogger("hsx.ml.dataset_exporter")
SECRET_PATTERN = re.compile(
    r"(?i)(\b[\w.-]*?(?:password|passwd|secret|api[_ -]?key|access[_ -]?token|"
    r"client[_ -]?secret|secret[_ -]?key|private[_ -]?key|token)\b[\"']?\s*[:=]\s*[\"']?)"
    r"([^\s,;\"'}]+)"
)
BEARER_PATTERN = re.compile(
    r"(?i)(\bauthorization\b[\"']?\s*[:=]\s*[\"']*bearer\s+)([^\s,;\"'}]+)"
)
MAX_PROMPT_CHARS = 6000
MAX_RESPONSE_CHARS = 12_000
MAX_TRAJECTORY_ITEMS = 128


def _redact(value: str, limit: int) -> str:
    value = SECRET_PATTERN.sub(r"\1[REDACTED]", value.strip())
    return BEARER_PATTERN.sub(r"\1[REDACTED]", value)[:limit]


def _tool_trajectory(history: list | None) -> list[dict[str, str]]:
    trajectory = []
    for message in (history or [])[-MAX_TRAJECTORY_ITEMS:]:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = block.get("name")
                if isinstance(name, str):
                    trajectory.append({"kind": "tool_call", "tool": name[:100]})
            elif block.get("type") == "tool_result":
                trajectory.append({"kind": "tool_result", "status": "recorded"})
        if role == "assistant" and any(
            isinstance(block, dict) and block.get("type") == "tool_calls"
            for block in content
        ):
            trajectory.append({"kind": "tool_call", "tool": "provider_tool_call"})
    return trajectory[:MAX_TRAJECTORY_ITEMS]


def _failed_tool_result(history: list | None) -> bool:
    saw_failure = False
    for message in history or []:
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            continue
        for block in message["content"]:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            content = block.get("content")
            try:
                result = json.loads(content) if isinstance(content, str) else content
            except json.JSONDecodeError:
                continue
            if not isinstance(result, dict):
                continue
            if result.get("success") is False or result.get("allowed") is False:
                saw_failure = True
            elif saw_failure and result.get("success") is True:
                return True
    return False


def _current_history(prompt: str, history: list | None) -> list:
    messages = history or []
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if (
            isinstance(message, dict)
            and message.get("role") == "user"
            and message.get("content") == prompt
        ):
            return messages[index:]
    return messages[-MAX_TRAJECTORY_ITEMS:]


class DatasetExporter:
    """Append redacted successful traces in a ShareGPT-compatible JSONL format."""

    def __init__(self, path: Path | None = None):
        self.path = path or DATA_DIR / "sft_dataset.jsonl"
        self._lock = threading.Lock()

    def record_success(
        self,
        prompt: str,
        response: str,
        *,
        job_id: str = "",
        history: list | None = None,
        sensitive: bool = False,
        recovered_error: str = "",
    ) -> bool:
        if sensitive or not isinstance(prompt, str) or not isinstance(response, str):
            return False
        safe_prompt = _redact(prompt, MAX_PROMPT_CHARS)
        safe_response = _redact(response, MAX_RESPONSE_CHARS)
        if not safe_prompt or not safe_response:
            return False
        job_history = _current_history(prompt, history)
        entry: dict[str, Any] = {
            "format": "sharegpt",
            "timestamp": datetime.now(UTC).isoformat(),
            "conversations": [
                {"from": "human", "value": safe_prompt},
                {"from": "assistant", "value": safe_response},
            ],
            "tool_trajectory": _tool_trajectory(job_history),
        }
        if job_id:
            entry["job_id"] = job_id[:100]
        error_type = recovered_error or ("tool_execution_error" if _failed_tool_result(job_history) else "")
        if error_type:
            entry["self_healed_error_type"] = error_type[:120]
        encoded = (json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                view = memoryview(encoded)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("Could not append SFT record.")
                    view = view[written:]
                os.fsync(descriptor)
            except OSError:
                LOGGER.exception("Could not append a successful task to the SFT dataset.")
                raise
            finally:
                os.close(descriptor)
        return True


SFT_DATASET = DatasetExporter()
