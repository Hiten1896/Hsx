from __future__ import annotations

import json
import logging
import re
import threading
from datetime import UTC, datetime
from pathlib import Path

from config import DATA_DIR

LOGGER = logging.getLogger("hsx.ml.intent_learning")
SECRET_PATTERN = re.compile(
    r"(?i)\b(password|passwd|secret|api[_ -]?key|token)\s*[:=]\s*\S+"
)


def _redact_prompt(prompt: str) -> str:
    prompt = prompt.strip()[:1200]
    return SECRET_PATTERN.sub(r"\1=[REDACTED]", prompt)


class UncertainIntentQueue:
    def __init__(self, path: Path | None = None):
        self.path = path or DATA_DIR / "intent_uncertain.jsonl"
        self._lock = threading.Lock()
        self._pending: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        with self.path.open(encoding="utf-8") as source:
            for line in source:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    LOGGER.warning("Skipping malformed uncertain-intent record.")
                    continue
                if not isinstance(event, dict) or not event.get("job_id"):
                    continue
                job_id = str(event["job_id"])
                if event.get("event") == "uncertain_intent":
                    self._pending[job_id] = event
                elif event.get("event") == "intent_resolution":
                    self._pending.pop(job_id, None)

    def _append(self, event: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as destination:
            destination.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            destination.flush()

    def queue(self, job_id: str, prompt: str, intent: str, confidence: float) -> None:
        if not job_id:
            return
        if not 0.4 <= confidence <= 0.6:
            raise ValueError("Only intent confidence in the uncertainty band can be queued.")
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": "uncertain_intent",
            "job_id": job_id,
            "prompt": _redact_prompt(prompt),
            "predicted_intent": intent[:100],
            "confidence": round(confidence, 6),
        }
        with self._lock:
            self._append(event)
            self._pending[job_id] = event

    def resolve(self, job_id: str, resolved_intent: str = "", *, succeeded: bool) -> None:
        if not job_id:
            return
        with self._lock:
            if job_id not in self._pending:
                return
            event = {
                "timestamp": datetime.now(UTC).isoformat(),
                "event": "intent_resolution",
                "job_id": job_id,
                "resolved_intent": resolved_intent[:100],
                "succeeded": bool(succeeded),
            }
            self._append(event)
            self._pending.pop(job_id, None)

    def offline_examples(self) -> list[tuple[str, str]]:
        pending: dict[str, dict] = {}
        examples = []
        if not self.path.is_file():
            return examples
        with self.path.open(encoding="utf-8") as source:
            for line in source:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                job_id = event.get("job_id")
                if event.get("event") == "uncertain_intent":
                    pending[job_id] = event
                elif event.get("event") == "intent_resolution":
                    original = pending.pop(job_id, None)
                    resolved = event.get("resolved_intent")
                    if original and event.get("succeeded") and isinstance(resolved, str) and resolved:
                        examples.append((original["prompt"], resolved))
        return examples


UNCERTAIN_INTENTS = UncertainIntentQueue()
