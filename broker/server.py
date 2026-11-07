"""Typed Unix socket broker."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

from agent.autonomy import needs_permission

from .audit import AuditWriter
from .executors import Executor
from .interfaces import SystemInterface, default_hsx_uid, default_system
from .policy import Policy, PolicyError
from .schema import SchemaError, validate_request


class Broker:
    def __init__(
        self,
        policy_path: Path,
        audit_path: Path,
        *,
        system: SystemInterface | None = None,
        expected_uid: int | None = None,
    ):
        self.policy_path = policy_path
        self.audit = AuditWriter(audit_path)
        self.system = system or default_system()
        self.expected_uid = default_hsx_uid() if expected_uid is None else expected_uid
        self.executor = Executor(self.system)

    def handle(self, request: Any, *, peer_uid: int | None = None) -> dict[str, Any]:
        request_id = request.get("id", "") if isinstance(request, dict) else ""
        action = request.get("action", "") if isinstance(request, dict) else ""
        args = request.get("args", {}) if isinstance(request, dict) else {}
        decision = "deny"
        try:
            if self.expected_uid is not None and peer_uid != self.expected_uid:
                raise PermissionError("Caller is not the hsx user.")
            action, args = validate_request(request)
            policy = Policy.load(self.policy_path)
            action_policy = policy.action(action)
            self._check_constraints(action, args, action_policy.constraints)
            outside_service_allowlist = action == "service" and not self._matches_any(
                args["unit"], action_policy.constraints.get("allowed_units", ["hsx-*"])
            )
            if (action_policy.permission and needs_permission(action_policy.permission)) or outside_service_allowlist:
                decision = "approval_required"
                result = "owner approval required"
            else:
                decision = "allow"
                result = self.executor.execute(action, args)
            response = {"id": request_id, "ok": decision == "allow", "decision": decision, "result": result}
        except (PermissionError, PolicyError, SchemaError, ValueError, RuntimeError) as exc:
            response = {"id": request_id, "ok": False, "decision": decision, "error": str(exc)}
        self.audit.write(
            request_id=request_id,
            action=action,
            args=args if isinstance(args, dict) else {},
            decision=response["decision"],
            result=response.get("result", response.get("error", "")),
        )
        return response

    @staticmethod
    def _matches_any(value: str, patterns: list[str]) -> bool:
        return any(value.startswith(prefix.rstrip("*")) for prefix in patterns)

    @staticmethod
    def _check_constraints(
        action: str,
        args: dict[str, Any],
        constraints: dict[str, Any],
    ) -> None:
        if action == "service" and args["operation"] not in {"start", "stop", "restart", "status"}:
            raise PermissionError("Service operation is not allowed.")
        if action == "schedule_timer" and args["operation"] not in {"start", "stop", "enable", "disable"}:
            raise PermissionError("Timer operation is not allowed.")
        if action == "schedule_timer" and not any(
            args["name"].startswith(prefix.rstrip("*")) for prefix in constraints.get("allowed_names", ["hsx-*"])
        ):
            raise PermissionError("Timer is outside policy.")
        if action == "journal_read" and not any(
            args["unit"].startswith(prefix.rstrip("*")) for prefix in constraints.get("allowed_units", ["hsx-*"])
        ):
            raise PermissionError("Unit is outside policy.")
        if action == "disk_cleanup" and args["target"] not in constraints.get(
            "allowed_targets", ["apt-cache", "old-logs", "outputs"]
        ):
            raise PermissionError("Cleanup target is outside policy.")
        if action in {"apt_install", "apt_remove", "pip_install"}:
            allowed = constraints.get("allowed_packages", ["[A-Za-z0-9][A-Za-z0-9+_.-]*"])
            import re

            if not any(re.fullmatch(pattern, args["package"]) for pattern in allowed):
                raise PermissionError("Package is outside policy.")


def serve(socket_path: Path, broker: Broker) -> None:
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        socket_path.unlink()
    except FileNotFoundError:
        pass
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(socket_path))
        listener.listen(8)
        while True:
            connection, _ = listener.accept()
            with connection:
                payload = connection.recv(64 * 1024)
                try:
                    request = json.loads(payload.decode("utf-8"))
                    response = broker.handle(request, peer_uid=broker.system.peer_uid(connection))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    response = {"ok": False, "decision": "deny", "error": "Invalid JSON."}
                connection.sendall((json.dumps(response) + "\n").encode("utf-8"))
