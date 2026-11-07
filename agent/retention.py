from __future__ import annotations

import json
import math
import os
import tempfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent.ask_user import AskUserJobs
from agent.conversations import ConversationStore
from agent.feedback import FeedbackStore
from agent.jobs import JobQueue
from agent.memory import MemoryManager

RETENTION_DAYS = 7
RETENTION_INTERVAL_SECONDS = 60 * 60


def _record_is_expired(line: str, cutoff: datetime, fallback_time: datetime) -> bool:
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return fallback_time < cutoff
    if not isinstance(record, dict):
        return fallback_time < cutoff
    timestamp = next(
        (
            record[key]
            for key in ("timestamp", "created_at", "created")
            if record.get(key) is not None
        ),
        None,
    )
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        if not math.isfinite(timestamp):
            return fallback_time < cutoff
        try:
            return datetime.fromtimestamp(timestamp, UTC) < cutoff
        except (OverflowError, OSError, ValueError):
            return fallback_time < cutoff
    if not isinstance(timestamp, str):
        return fallback_time < cutoff
    try:
        recorded_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return fallback_time < cutoff
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=UTC)
    return recorded_at < cutoff


def _prune_jsonl(path: Path, cutoff: datetime) -> int:
    if not path.is_file():
        return 0
    modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    with path.open(encoding="utf-8") as source:
        lines = source.readlines()
    retained = [
        line for line in lines
        if not _record_is_expired(line, cutoff, modified)
    ]
    removed = len(lines) - len(retained)
    if not removed:
        return 0
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}-",
        suffix=".tmp",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as destination:
            destination.writelines(retained)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return removed


def _write_personalization_summary(
    memory_dir: Path,
    feedback_records: list[dict],
    conversation_count: int,
    training_examples: int,
    tool_usage: Counter,
    cutoff: datetime,
) -> None:
    ratings = Counter(record["rating"] for record in feedback_records)
    tool_ratings: dict[str, Counter] = {}
    for record in feedback_records:
        for tool in record.get("metadata", {}).get("tool_names", []):
            tool_ratings.setdefault(str(tool), Counter())[record["rating"]] += 1
    summary = {
        "updated_at": datetime.now(UTC).isoformat(),
        "window_start": cutoff.isoformat(),
        "conversation_count": conversation_count,
        "feedback_count": sum(ratings.values()),
        "ratings": dict(ratings),
        "training_examples": training_examples,
        "tool_usage": dict(sorted(tool_usage.items())),
        "tool_ratings": {
            tool: dict(counts)
            for tool, counts in sorted(tool_ratings.items())
        },
        "source": "Local, redacted feedback and interaction metadata; no provider call.",
    }
    memory_dir.mkdir(parents=True, exist_ok=True)
    target = memory_dir / "personalization.json"
    descriptor, temporary_name = tempfile.mkstemp(
        dir=memory_dir,
        prefix=".personalization-",
        suffix=".tmp",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as destination:
            json.dump(summary, destination, ensure_ascii=False, indent=2)
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary_path, target)
    finally:
        temporary_path.unlink(missing_ok=True)


def _recent_training_summary(data_dir: Path, cutoff: datetime) -> tuple[int, Counter[str]]:
    examples = 0
    tool_usage: Counter[str] = Counter()
    dataset_dir = data_dir / "datasets"
    if not dataset_dir.is_dir():
        return examples, tool_usage
    for path in dataset_dir.glob("*dataset.jsonl"):
        modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        with path.open(encoding="utf-8") as source:
            for line in source:
                if _record_is_expired(line, cutoff, modified):
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                examples += 1
                trajectory = record.get("tool_trajectory", [])
                if not isinstance(trajectory, list):
                    continue
                for step in trajectory:
                    if isinstance(step, dict) and step.get("kind") == "tool_call":
                        tool = step.get("tool")
                        if isinstance(tool, str):
                            tool_usage[tool] += 1
    return examples, tool_usage


def run_retention_maintenance(
    *,
    feedback_store: FeedbackStore,
    conversations: ConversationStore,
    memory: MemoryManager,
    ask_jobs: AskUserJobs | None = None,
    jobs: JobQueue | None = None,
    data_dir: Path,
    logs_dir: Path,
    memory_dir: Path,
    now: datetime | None = None,
) -> dict[str, int]:
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)
    cutoff = current_time.astimezone(UTC) - timedelta(days=RETENTION_DAYS)
    cutoff_text = cutoff.isoformat()

    records = feedback_store.export_records(since=cutoff_text)
    recent_conversations = [
        item for item in conversations.list()
        if str(item.get("updated", "")) >= cutoff_text
    ]
    training_examples, tool_usage = _recent_training_summary(data_dir, cutoff)
    _write_personalization_summary(
        memory_dir,
        records,
        len(recent_conversations),
        training_examples,
        tool_usage,
        cutoff,
    )

    removed = {
        "feedback": feedback_store.prune_before(cutoff_text),
        "conversations": conversations.prune_before(cutoff_text),
        "memory_messages": memory.prune_messages_before(cutoff_text),
        "ask_jobs": ask_jobs.prune_before(cutoff_text) if ask_jobs else 0,
        "jobs": jobs.prune_before(cutoff_text) if jobs else 0,
        "training_records": 0,
    }
    for root in (data_dir, logs_dir):
        if not root.is_dir():
            continue
        for path in root.rglob("*.jsonl*"):
            if path.is_file():
                count = _prune_jsonl(path, cutoff)
                if "datasets" in path.parts:
                    removed["training_records"] += count
    return removed
