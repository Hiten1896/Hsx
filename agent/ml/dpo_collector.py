from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.ml.dataset_exporter import MAX_PROMPT_CHARS, MAX_RESPONSE_CHARS, _redact
from agent.ml.log_recorder import LOG_RECORDER
from config import DATA_DIR

LOGGER = logging.getLogger("hsx.ml.dpo_collector")
MAX_PENDING_PROMPTS = 128
MAX_SEEN_IDS = 2048
MAX_CANDIDATE_BYTES = 8 * 1024 * 1024
MAX_DATASET_BYTES = 64 * 1024 * 1024
MAX_CANDIDATE_HISTORY = 256
MAX_TRACE_ITEMS = 128
TRACEBACK_MARKER = "Traceback (most recent call last):"
TEST_TOOL_NAMES = {"pytest", "run_tests", "unit_tests", "test_suite"}


def _prompt_key(prompt: str) -> str:
    normalized = " ".join(prompt.casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _current_history(prompt: str, history: list | None) -> list:
    messages = history or []
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if (
            isinstance(message, dict)
            and message.get("role") == "user"
            and message.get("content") == prompt
        ):
            return messages[index:][-MAX_TRACE_ITEMS:]
    return messages[-MAX_TRACE_ITEMS:]


def _decoded_result(value: object) -> object:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError:
        return value


def _has_passed_tests(tool_name: str, result: object) -> bool:
    if not isinstance(result, dict):
        return False
    for key in ("tests_passed", "unit_tests_passed"):
        if result.get(key) is True:
            return True
    proposal = result.get("proposal")
    for candidate in (result, proposal if isinstance(proposal, dict) else {}):
        checks = candidate.get("checks")
        if isinstance(checks, list) and checks and all(
            isinstance(check, dict) and check.get("passed") is True
            for check in checks
        ):
            return True
    return tool_name.casefold() in TEST_TOOL_NAMES and result.get("success") is True


def _check_groups(result: dict[str, Any]) -> list:
    groups = [result.get("checks")]
    proposal = result.get("proposal")
    if isinstance(proposal, dict):
        groups.append(proposal.get("checks"))
    return [checks for checks in groups if isinstance(checks, list)]


def execution_evidence(
    history: list | None,
    *,
    succeeded: bool,
    error: str = "",
) -> dict[str, Any]:
    """Derive conservative preference labels from structured tool/test results."""
    messages = history or []
    tool_names: dict[str, str] = {}
    tool_results: dict[str, tuple[str, object]] = {}
    lint_errors = False
    traceback_seen = TRACEBACK_MARKER in error
    tests_passed: bool | None = None
    orphaned_tool_result = False
    error_text = error.casefold()
    if any(marker in error_text for marker in ("ruff error", "ruff check failed", "lint error", "lint failed")):
        lint_errors = True

    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        if message.get("role") == "assistant":
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    call_id, name = block.get("id"), block.get("name")
                    if isinstance(call_id, str) and isinstance(name, str):
                        tool_names[call_id] = name
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            call_id = block.get("tool_use_id")
            raw_result = block.get("content")
            result = _decoded_result(raw_result)
            text = raw_result if isinstance(raw_result, str) else json.dumps(result, default=str)
            traceback_seen = traceback_seen or TRACEBACK_MARKER in text
            orphaned_tool_result = orphaned_tool_result or call_id not in tool_names
            tool_name = tool_names.get(call_id, "")
            tool_results[str(call_id)] = (tool_name, result)
            if _has_passed_tests(tool_name, result):
                tests_passed = True
            if isinstance(result, dict):
                if result.get("tests_passed") is False or result.get("unit_tests_passed") is False:
                    tests_passed = False
                if result.get("lint_passed") is False or result.get("lint_errors"):
                    lint_errors = True
                for checks in _check_groups(result):
                    for check in checks:
                        if isinstance(check, dict):
                            if check.get("passed") is False:
                                tests_passed = False
                            if "ruff" in str(check.get("command", "")).casefold() and check.get("passed") is False:
                                lint_errors = True
            if any(marker in text.casefold() for marker in ("ruff error", "lint error", "lint failed")):
                lint_errors = True

    tool_calls = len(tool_names)
    all_tools_succeeded = not orphaned_tool_result and all(
        call_id in tool_results
        and not (
            isinstance(result, dict)
            and (result.get("success") is False or result.get("allowed") is False)
        )
        for call_id in tool_names
        for _, result in [tool_results.get(call_id, ("", None))]
    )
    tracebacks_or_retries = traceback_seen or orphaned_tool_result or any(
        isinstance(result, dict)
        and (
            result.get("success") is False
            or result.get("allowed") is False
            or bool(result.get("traceback"))
        )
        for _, result in tool_results.values()
    )
    unhandled_exception = not succeeded
    rejected = unhandled_exception or tracebacks_or_retries or lint_errors or tests_passed is False
    chosen = tests_passed is True and all_tools_succeeded and not rejected
    preference = "rejected" if rejected else "chosen" if chosen else None
    return {
        "preference": preference,
        "tests_passed": tests_passed,
        "tool_execution_succeeded": all_tools_succeeded,
        "tool_call_count": tool_calls,
        "traceback_or_tool_failure": tracebacks_or_retries,
        "lint_errors": lint_errors,
        "unhandled_exception": unhandled_exception,
    }


class DPOCollector:
    """Persist labeled execution candidates and pair only matching prompts."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        candidates_path: Path | None = None,
    ):
        self.path = Path(path or DATA_DIR / "datasets" / "dpo_dataset.jsonl")
        self.candidates_path = Path(
            candidates_path or self.path.with_name("dpo_candidates.jsonl")
        )
        self._lock = threading.Lock()
        self._pending: OrderedDict[str, dict[str, dict[str, str]]] = OrderedDict()
        self._seen_runs: OrderedDict[str, None] = OrderedDict()
        self._seen_pairs: OrderedDict[str, None] = OrderedDict()
        self._load_recent_state()

    def _load_recent_state(self) -> None:
        if self.path.is_file():
            for item in self._tail_records(self.path):
                chosen_id, rejected_id = item.get("chosen_run_id"), item.get("rejected_run_id")
                if isinstance(chosen_id, str) and isinstance(rejected_id, str):
                    self._remember_id(self._seen_pairs, f"{chosen_id}:{rejected_id}")
        if not self.candidates_path.is_file():
            return
        for item in self._tail_records(self.candidates_path):
            run_id, key, preference = item.get("run_id"), item.get("prompt_key"), item.get("preference")
            if not all(isinstance(value, str) for value in (run_id, key, preference)):
                continue
            if preference not in {"chosen", "rejected"}:
                continue
            self._remember_id(self._seen_runs, run_id)
            self._remember(key, preference, item.get("prompt", ""), item.get("candidate", ""), run_id)

    @staticmethod
    def _remember_id(collection: OrderedDict[str, None], value: str) -> None:
        collection[value] = None
        collection.move_to_end(value)
        while len(collection) > MAX_SEEN_IDS:
            collection.popitem(last=False)

    @staticmethod
    def _tail_records(path: Path) -> list[dict[str, Any]]:
        with path.open("rb") as source:
            source.seek(0, os.SEEK_END)
            size = source.tell()
            source.seek(max(0, size - 192 * 1024))
            raw_lines = source.read().splitlines()[-MAX_CANDIDATE_HISTORY:]
        records = []
        for line in raw_lines:
            try:
                item = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(item, dict):
                records.append(item)
        return records

    def _remember(
        self,
        key: str,
        preference: str,
        prompt: str,
        candidate: str,
        run_id: str,
    ) -> None:
        candidates = self._pending.setdefault(key, {})
        candidates[preference] = {
            "prompt": prompt,
            "candidate": candidate,
            "run_id": run_id,
        }
        self._pending.move_to_end(key)
        while len(self._pending) > MAX_PENDING_PROMPTS:
            self._pending.popitem(last=False)

    @staticmethod
    def _append_jsonl(
        path: Path,
        entry: dict[str, Any],
        *,
        sync: bool = False,
        max_bytes: int = MAX_DATASET_BYTES,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = (json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        if len(encoded) > max_bytes:
            raise ValueError("DPO record exceeds the configured file size limit.")
        if path.exists() and path.stat().st_size + len(encoded) > max_bytes:
            backup = path.with_name(f"{path.stem}.1{path.suffix}")
            backup.unlink(missing_ok=True)
            os.replace(path, backup)
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            remaining = memoryview(encoded)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("Could not append DPO JSONL record.")
                remaining = remaining[written:]
            if sync:
                os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def record_execution(
        self,
        prompt: str,
        response: str,
        *,
        run_id: str,
        history: list | None = None,
        succeeded: bool,
        error: str = "",
        sensitive: bool = False,
    ) -> dict[str, Any] | None:
        if (
            not isinstance(prompt, str)
            or not isinstance(response, str)
            or not isinstance(run_id, str)
            or not run_id.strip()
        ):
            return None
        scoped_history = _current_history(prompt, history)
        evidence = execution_evidence(scoped_history, succeeded=succeeded, error=error)
        preference = evidence["preference"]
        LOG_RECORDER.record_event(
            "execution_evidence",
            {
                "run_id": run_id,
                "preference": preference or "unclassified",
                "evidence": evidence,
                "prompt_key": _prompt_key(prompt) if prompt else "",
            },
            sensitive=sensitive,
        )
        if sensitive or preference is None:
            return None
        safe_prompt = _redact(prompt, MAX_PROMPT_CHARS)
        candidate = response.strip() or (f"Execution failed: {error}" if error else "")
        candidate = _redact(candidate, MAX_RESPONSE_CHARS)
        if not safe_prompt or not candidate:
            return None
        key = _prompt_key(safe_prompt)
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "run_id": run_id[:100],
            "prompt_key": key,
            "preference": preference,
            "prompt": safe_prompt,
            "candidate": candidate,
            "evidence": evidence,
        }
        with self._lock:
            if run_id in self._seen_runs:
                return None
            self._append_jsonl(
                self.candidates_path,
                entry,
                max_bytes=MAX_CANDIDATE_BYTES,
            )
            self._remember_id(self._seen_runs, run_id)
            self._remember(key, preference, safe_prompt, candidate, run_id)
            other_preference = "rejected" if preference == "chosen" else "chosen"
            other = self._pending[key].get(other_preference)
            if other is None:
                pair = None
            else:
                chosen = entry if preference == "chosen" else other
                rejected = entry if preference == "rejected" else other
                pair_id = f"{chosen['run_id']}:{rejected['run_id']}"
                if pair_id in self._seen_pairs:
                    pair = None
                else:
                    pair = {
                        "format": "dpo",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "prompt": safe_prompt,
                        "chosen": chosen["candidate"],
                        "rejected": rejected["candidate"],
                        "chosen_run_id": chosen["run_id"],
                        "rejected_run_id": rejected["run_id"],
                        "evidence": {
                            "chosen": chosen.get("evidence", {}),
                            "rejected": rejected.get("evidence", {}),
                        },
                    }
                    self._append_jsonl(self.path, pair, sync=True)
                    self._remember_id(self._seen_pairs, pair_id)
        LOG_RECORDER.record_event(
            "dpo_candidate",
            {"run_id": run_id, "preference": preference, "prompt_key": key},
        )
        return pair or entry


def export_clean_pairs(
    source: Path,
    destination: Path,
    *,
    limit: int = 10_000,
) -> int:
    """Copy valid, redacted DPO pairs to an offline-training JSONL file."""
    if not 1 <= limit <= 10_000:
        raise ValueError("limit must be between 1 and 10000.")
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    seen: set[str] = set()
    count = 0
    try:
        with source.open(encoding="utf-8") as input_file, os.fdopen(
            descriptor, "w", encoding="utf-8", newline="\n"
        ) as output_file:
            for line in input_file:
                if count >= limit:
                    break
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict) or item.get("format") != "dpo":
                    continue
                prompt, chosen, rejected = (
                    item.get("prompt"),
                    item.get("chosen"),
                    item.get("rejected"),
                )
                if not all(isinstance(value, str) and value.strip() for value in (prompt, chosen, rejected)):
                    continue
                cleaned = {
                    "format": "dpo",
                    "prompt": _redact(prompt, MAX_PROMPT_CHARS),
                    "chosen": _redact(chosen, MAX_RESPONSE_CHARS),
                    "rejected": _redact(rejected, MAX_RESPONSE_CHARS),
                }
                key = hashlib.sha256(
                    json.dumps(
                        [cleaned["prompt"], cleaned["chosen"], cleaned["rejected"]],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                if key in seen or cleaned["chosen"] == cleaned["rejected"]:
                    continue
                seen.add(key)
                output_file.write(json.dumps(cleaned, ensure_ascii=False, sort_keys=True) + "\n")
                count += 1
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(temporary_name, destination)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return count


DPO_COLLECTOR = DPOCollector()
