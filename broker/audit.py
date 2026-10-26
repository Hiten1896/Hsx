"""Append-only broker audit records with secret-safe argument summaries."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


class AuditWriter:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    @staticmethod
    def _summary(args: dict[str, Any]) -> dict[str, str]:
        return {key: f"<{type(value).__name__}>" for key, value in args.items()}

    def write(
        self,
        *,
        request_id: str,
        action: str,
        args: dict[str, Any],
        tier: str,
        decision: str,
        result: str,
    ) -> None:
        entry = {
            "id": request_id or str(uuid4()),
            "timestamp": datetime.now(UTC).isoformat(),
            "action": action,
            "args_summary": self._summary(args),
            "tier": tier,
            "decision": decision,
            "result": result,
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
