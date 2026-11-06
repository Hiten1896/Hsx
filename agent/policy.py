import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.approval import ApprovalGuard
from agent.autonomy import autonomy_mode, classify_action
from config import BASE_DIR


class PolicyEngine:
    def __init__(self, audit_path: Path | None = None, approval_guard: ApprovalGuard | None = None):
        self.audit_path = audit_path or (BASE_DIR / "logs" / "audit.jsonl")
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self.approval_guard = approval_guard

    @staticmethod
    def dangerous_override_enabled() -> bool:
        value = os.getenv("HSX_ALLOW_DANGEROUS", "").strip().lower()
        if value in {"1", "true", "yes", "on", "allow", "override"}:
            return True
        return os.getenv("HSX_ALLOW_SYSTEM_CONTROL", "").strip().lower() in {
            "1", "true", "yes", "on", "allow", "override"
        }

    def stop_all(self) -> None:
        self._stopped.set()

    def reset_stop(self) -> None:
        self._stopped.clear()

    def is_stopped(self) -> bool:
        return self._stopped.is_set()

    @staticmethod
    def _fingerprint(arguments: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(arguments, sort_keys=True, default=str).encode()).hexdigest()

    def _audit(
        self,
        session: str,
        tool: str,
        arguments: dict,
        tier: str,
        decision: str,
        result: str,
        approver: str = "system",
    ) -> None:
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "session": session,
            "tool": tool,
            "args_summary": {key: type(value).__name__ for key, value in arguments.items()},
            "tier": tier,
            "risk_tier": tier.casefold(),
            "decision": decision,
            "approver": approver,
            "result_status": result,
        }
        with self._lock, self.audit_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, sort_keys=True) + "\n")

    def audit_event(self, event: str, session: str, job_id: str, status: str) -> None:
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "session": session,
            "job_id": job_id,
            "event": event,
            "tier": "WORKFLOW",
            "risk_tier": "workflow",
            "decision": status,
            "approver": "system",
            "result_status": status,
        }
        with self._lock, self.audit_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, sort_keys=True) + "\n")

    def decide(
        self,
        skill: dict,
        arguments: dict,
        session: str,
        approved: bool | str | None = None,
        *,
        approver: str = "system",
    ) -> dict:
        metadata_risk = skill.get("risk", "confirm")
        if metadata_risk not in {"safe", "confirm", "dangerous"}:
            self._audit(session, skill.get("name", "unknown"), arguments, "BLOCKED", "deny", "invalid_risk")
            return {"allowed": False, "status": "denied"}

        classification = classify_action(skill["name"], arguments, metadata_risk)
        tier = classification["tier"]
        if tier == "BLOCKED":
            self._audit(session, skill["name"], arguments, tier, "deny", "blocked")
            return {"allowed": False, "status": "denied"}

        mode = autonomy_mode()
        if approved is False:
            self._audit(session, skill["name"], arguments, tier, "cancel", "denied", approver)
            return {"allowed": False, "status": "cancelled"}

        if isinstance(approved, str) and self.approval_guard is not None:
            if self.approval_guard.verify(approved):
                approved = True
                approver = "owner_pin"
            else:
                approved = None

        if approved is True:
            self._audit(session, skill["name"], arguments, tier, "approved", "approved", approver)
            return {"allowed": True, "status": "approved"}

        if tier == "SAFE" or (tier == "NORMAL" and mode in {"auto", "strict"}):
            self._audit(session, skill["name"], arguments, tier, "allow", "allowed")
            return {"allowed": True, "status": "allowed"}

        self._audit(session, skill["name"], arguments, tier, "ask", "waiting_approval")
        return {
            "allowed": False,
            "status": "waiting_approval",
            "approval_request": {
                "tool": skill["name"],
                "summary": classification["summary"],
                "why": classification["why"],
                "risk": tier,
                "details": classification["details"],
            },
        }


POLICY = PolicyEngine()
