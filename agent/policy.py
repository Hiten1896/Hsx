import fnmatch
import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from agent.approval import ApprovalGuard
from agent.autonomy import (
    PERMISSION_FILE,
    add_always_allow,
    classify_action,
    is_hard_denied,
    needs_permission,
    permission_rules,
)
from config import BASE_DIR


class PolicyEngine:
    def __init__(self, audit_path: Path | None = None, approval_guard: ApprovalGuard | None = None,
                 permission_path: Path | None = None):
        self.audit_path = audit_path or (BASE_DIR / "logs" / "audit.jsonl")
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self.approval_guard = approval_guard
        self.permission_path = permission_path or PERMISSION_FILE

    def stop_all(self) -> None:
        self._stopped.set()

    def reset_stop(self) -> None:
        self._stopped.clear()

    def is_stopped(self) -> bool:
        return self._stopped.is_set()

    def _audit(self, session: str, tool: str, arguments: dict, action: str, decision: str,
               result: str, approver: str = "system") -> None:
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "session": session,
            "tool": tool,
            "args_summary": {key: type(value).__name__ for key, value in arguments.items()},
            "action": action,
            "decision": decision,
            "approver": approver,
            "result_status": result,
        }
        with self._lock, self.audit_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, sort_keys=True) + "\n")

    def audit_event(self, event: str, session: str, job_id: str, status: str) -> None:
        entry = {
            "timestamp": datetime.now(UTC).isoformat(), "session": session,
            "job_id": job_id, "event": event, "decision": status,
            "approver": "system", "result_status": status,
        }
        with self._lock, self.audit_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, sort_keys=True) + "\n")

    def permission_list(self) -> dict:
        return permission_rules(self.permission_path)

    def update_permission(self, action: str, enabled: bool) -> dict:
        from agent.autonomy import set_permission_action

        rules = set_permission_action(action, enabled, path=self.permission_path)
        self.audit_event("permission_policy_changed", "settings", action, "enabled" if enabled else "disabled")
        return rules

    def remove_always_allow(self, pattern: str) -> dict:
        from agent.autonomy import remove_always_allow

        rules = remove_always_allow(pattern, path=self.permission_path)
        self.audit_event("always_allow_removed", "settings", pattern, "removed")
        return rules

    def reset_always_allow(self) -> dict:
        rules = permission_rules(self.permission_path)
        rules["always_allow"] = []
        self.permission_path.write_text(
            "ask_first:\n" + "".join(f"  - {item}\n" for item in rules["ask_first"]) + "always_allow: []\n",
            encoding="utf-8",
        )
        self.audit_event("always_allow_reset", "settings", "", "reset")
        return rules

    def decide(self, skill: dict, arguments: dict, session: str,
               approved: bool | str | None = None, *, approver: str = "system") -> dict:
        name = str(skill.get("name", "unknown"))
        action = classify_action(name, arguments)
        camera_already_running = False
        if action == "camera_start":
            from agent.hardware.camera import CAMERA, camera_session_owner

            camera_already_running = CAMERA.has_owner(camera_session_owner(session))
        if is_hard_denied(name, arguments):
            self._audit(session, name, arguments, action, "hard_deny", "denied")
            return {"allowed": False, "status": "denied", "error": "Hsx always blocks actions that expose secrets or disable its security controls."}
        if approved is False or approved == "reject":
            self._audit(session, name, arguments, action, "denied", "denied", approver)
            return {"allowed": False, "status": "cancelled"}
        if isinstance(approved, str) and approved not in {"always_allow", "always"} and self.approval_guard is not None:
            if self.approval_guard.verify(approved):
                approved = True
                approver = "owner_pin"
            else:
                approved = None
        if approved in {"always_allow", "always"}:
            if not action:
                self._audit(session, name, arguments, action, "hard_deny", "no_permission_pattern")
                return {"allowed": False, "status": "denied"}
            if action == "firmware_flash":
                self._audit(session, name, arguments, action, "approved", "allowed", approver)
                return {"allowed": True, "status": "approved"}
            add_always_allow(action, path=self.permission_path)
            self._audit(session, name, arguments, action, "always_allowed", "allowed", approver)
            return {"allowed": True, "status": "always_allowed"}
        if approved is True:
            self._audit(session, name, arguments, action, "approved", "allowed", approver)
            return {"allowed": True, "status": "approved"}
        rules = permission_rules(self.permission_path)
        has_saved_allow = any(fnmatch.fnmatchcase(action, pattern) for pattern in rules["always_allow"])
        ask = bool(
            action
            and (
                action == "firmware_flash"
                or needs_permission(action, path=self.permission_path)
            )
            and not camera_already_running
        )
        if not ask or (has_saved_allow and action != "firmware_flash"):
            self._audit(session, name, arguments, action, "always_allowed" if has_saved_allow else "allowed", "allowed")
            return {"allowed": True, "status": "always_allowed" if has_saved_allow else "allowed"}
        self._audit(session, name, arguments, action, "ask", "waiting_for_approval")
        summary = f"Hsx wants to: {self._plain_action(action, arguments)}."
        request = {
            "tool": name,
            "summary": summary,
            "why": "This action is listed in your editable permissions policy.",
            "action_pattern": action,
            "details": "You can allow this action once or always allow this action pattern in Settings > Permissions.",
        }
        if name == "capability_resolver":
            from agent.package_catalog import PACKAGE_CATALOG

            recipe = PACKAGE_CATALOG.get(str(arguments.get("package", "")))
            if recipe:
                request.update({
                    "request_type": "package_install",
                    "summary": f"Hsx wants to: install {arguments['package']}=={recipe['version']} in its own Python environment.",
                    "why": f"Adds {recipe['purpose']}; about {recipe['size_mb']} MB and needs the internet.",
                    "details": "Project virtual environment only. No sudo or system packages. Hsx will import the package to self-test it, then continue your request.",
                })
        if action == "firmware_flash":
            request.update({
                "request_type": "firmware_flash",
                "summary": "Hsx wants to write firmware to the connected ESP device.",
                "why": "Writing firmware changes the selected device and can erase or replace its current program.",
                "details": str(arguments.get("command", ""))[:500],
            })
        return {"allowed": False, "status": "waiting_approval", "approval_request": request}

    @staticmethod
    def _plain_action(action: str, arguments: dict) -> str:
        if action == "install_packages":
            return f"install {arguments.get('package', 'a package')} in its own Python environment"
        if action == "firmware_flash":
            return "write firmware to the selected ESP device"
        if action == "camera_start":
            return "start the camera for this request"
        if action == "microphone_start":
            return "start microphone input"
        if action == "external_services":
            return "send this request to an external service"
        return action.replace("_", " ")


POLICY = PolicyEngine()
