import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import BASE_DIR


class PolicyEngine:
    def __init__(self, audit_path: Path | None = None):
        self.audit_path = audit_path or (BASE_DIR / "logs" / "audit.jsonl")
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self._acknowledged: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()
        self._stopped = threading.Event()

    def stop_all(self) -> None:
        self._stopped.set()

    def reset_stop(self) -> None:
        self._stopped.clear()

    def is_stopped(self) -> bool:
        return self._stopped.is_set()

    @staticmethod
    def _fingerprint(arguments: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(arguments, sort_keys=True, default=str).encode()).hexdigest()

    def _audit(self, session: str, tool: str, arguments: dict, risk: str, decision: str, result: str, advisory: str = "") -> None:
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "session": session,
            "tool": tool,
            "args_summary": {key: str(value)[:120] for key, value in arguments.items()},
            "risk_tier": risk,
            "decision": decision,
            "result_status": result,
        }
        if advisory:
            entry["advisory"] = advisory
        with self._lock, self.audit_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, sort_keys=True) + "\n")

    def decide(self, skill: dict, arguments: dict, session: str, approved: bool | None = None) -> dict:
        risk = skill.get("risk", "confirm")
        advisory = str(skill.get("advisory", "")).strip()
        if len(advisory) >= 300:
            raise ValueError("Skill advisory must be under 300 characters.")
        requires_ack = bool(skill.get("requires_acknowledgement", False) or advisory)
        fingerprint = self._fingerprint(arguments)
        key = (session, skill["name"])
        with self._lock:
            already_acknowledged = self._acknowledged.get(key) == fingerprint
        if approved is False:
            self._audit(session, skill["name"], arguments, risk, "cancel", "denied", advisory)
            return {"allowed": False, "status": "cancelled"}
        if approved is True:
            if risk == "dangerous" and os.getenv("HSX_ALLOW_DANGEROUS") != "1":
                self._audit(session, skill["name"], arguments, risk, "deny", "dangerous_disabled", advisory)
                return {"allowed": False, "status": "dangerous_disabled"}
            with self._lock:
                self._acknowledged[key] = fingerprint
            self._audit(session, skill["name"], arguments, risk, "proceed", "approved", advisory)
            return {"allowed": True, "status": "approved"}
        if risk == "dangerous" or (not already_acknowledged and (requires_ack or risk == "confirm")):
            self._audit(session, skill["name"], arguments, risk, "ask", "waiting_approval", advisory)
            return {
                "allowed": False,
                "status": "waiting_approval",
                "approval_request": {
                    "tool": skill["name"],
                    "arguments": arguments,
                    "advisory": advisory,
                    "choices": ["I understand, proceed", "Cancel"],
                },
            }
        self._audit(session, skill["name"], arguments, risk, "allow", "approved", advisory)
        return {"allowed": True, "status": "allowed"}


POLICY = PolicyEngine()
