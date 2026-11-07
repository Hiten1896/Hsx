import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from agent.approval import ApprovalGuard
from agent.activity_trace import redact_activity_text
from agent.autonomy import classify_action, is_hard_denied
from config import BASE_DIR

APPROVAL_REQUIRED_ACTIONS = {
    "delete_owner_files",
    "firmware_flash",
    "overwrite_owner_files",
    "pipe_to_shell",
    "shutdown_reboot",
    "sudo",
    "system_services",
}


class PolicyEngine:
    def __init__(self, audit_path: Path | None = None, approval_guard: ApprovalGuard | None = None):
        self.audit_path = audit_path or (BASE_DIR / "logs" / "audit.jsonl")
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self.approval_guard = approval_guard

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

    def decide(self, skill: dict, arguments: dict, session: str,
               approved: bool | str | None = None, *, approver: str = "system") -> dict:
        name = str(skill.get("name", "unknown"))
        action = classify_action(name, arguments)
        if is_hard_denied(name, arguments) or action == "read_secrets":
            self._audit(session, name, arguments, action, "hard_deny", "denied")
            return {"allowed": False, "status": "denied", "error": "Hsx always blocks actions that expose secrets or disable its security controls."}
        if approved is False or approved == "reject":
            self._audit(session, name, arguments, action, "denied", "denied", approver)
            return {"allowed": False, "status": "cancelled"}
        if approved in {"always_allow", "always"}:
            approved = None
        if isinstance(approved, str) and self.approval_guard is not None:
            if self.approval_guard.verify(approved):
                approved = True
                approver = "owner_pin"
            else:
                approved = None
        if approved is True:
            self._audit(session, name, arguments, action, "approved", "allowed", approver)
            return {"allowed": True, "status": "approved"}
        if name == "ask_user":
            self._audit(session, name, arguments, action, "allowed", "allowed")
            return {"allowed": True, "status": "allowed"}

        if action not in APPROVAL_REQUIRED_ACTIONS:
            self._audit(session, name, arguments, action, "allowed", "allowed")
            return {"allowed": True, "status": "allowed"}

        self._audit(session, name, arguments, action, "ask", "waiting_for_approval")
        summary = f"Hsx wants to: {self._plain_action(action, name, arguments)}."
        request = {
            "tool": name,
            "summary": summary,
            "request_type": "tool_consent",
            "why": self._consequence(name, action),
            "details": f"Tool: {name}. Approval applies only to this action in the current task.",
            **self._review_details(name, action, arguments),
        }
        if name == "capability_resolver":
            from agent.package_catalog import PACKAGE_CATALOG

            recipe = PACKAGE_CATALOG.get(str(arguments.get("package", "")))
            if recipe:
                request.update({
                    "request_type": "package_install",
                    "summary": f"Hsx wants to: install {arguments['package']}=={recipe['version']} in its own Python environment.",
                    "why": f"Possible consequence: downloads about {recipe['size_mb']} MB and adds the package for {recipe['purpose']}.",
                    "details": "Project virtual environment only. No sudo or system packages. Hsx will import the package to self-test it, then continue your request.",
                })
        if action == "firmware_flash":
            request.update({
                "request_type": "firmware_flash",
                "summary": "Hsx wants to write firmware to the connected ESP device.",
                "why": "Possible consequence: writing firmware can erase or replace the selected device's current program.",
                "details": str(arguments.get("command", ""))[:500],
            })
        return {"allowed": False, "status": "waiting_approval", "approval_request": request}

    @staticmethod
    def _review_details(name: str, action: str, arguments: dict) -> dict:
        command = arguments.get("command") or arguments.get("code")
        review = {}
        if isinstance(command, str) and command.strip():
            review["command"] = redact_activity_text(command, 1200)

        if action == "firmware_flash":
            review.update({
                "host_impact": "Writes firmware to the selected connected device; it does not install software on the HP.",
                "security_risk": "The device's existing program may be erased or replaced. Confirm the target and firmware file.",
            })
        elif name in {"system_execute", "shell", "run_code"}:
            review.update({
                "host_impact": (
                    "Runs on this HP host as the Hsx service account. What changes depends on the command shown."
                ),
                "security_risk": (
                    "The command may read or change files and may use the network. Only approve it if the "
                    "command and its target are expected; privileged operations remain separately restricted."
                ),
            })
        elif action == "install_packages":
            review.update({
                "host_impact": "Downloads the approved package into Hsx's project environment, not system-wide.",
                "security_risk": "The package's code will run inside Hsx when used. Installation is limited to the pinned package shown.",
            })
        elif action == "external_services":
            review.update({
                "host_impact": "Uses the configured external service; no host settings are changed.",
                "security_risk": "The request and required data may be sent to that service for processing.",
            })
        elif action in {"delete_owner_files", "overwrite_owner_files"}:
            review.update({
                "host_impact": "May delete or replace the named workspace or host files.",
                "security_risk": "Existing data may be lost. Check the target path and keep a backup if needed.",
            })
        elif action == "sudo":
            review.update({
                "host_impact": "Attempts a system-level change on this HP host.",
                "security_risk": "Elevated privileges can affect the operating system and other users; approve only a clearly understood action.",
            })
        elif action in {"camera_start", "microphone_start"}:
            review.update({
                "host_impact": (
                    "Starts the HP camera for this browser session; the session lease expires after five minutes "
                    "unless you turn it off sooner."
                    if action == "camera_start"
                    else "Opens the HP microphone for this browser session; audio samples are discarded, not recorded or sent."
                ),
                "security_risk": (
                    "The live feed is available only to the session that approved camera access. A frame is sent to "
                    "the vision provider only when you ask Hsx to describe what the camera sees."
                    if action == "camera_start"
                    else "Nearby sound is briefly read by the host audio input callback and immediately discarded."
                ),
            })
        else:
            review.update({
                "host_impact": "Only the action described above is requested; no broader system change is implied.",
                "security_risk": "Review the tool and details before allowing it to access local or external data.",
            })
        return review

    @staticmethod
    def _plain_action(action: str, name: str, arguments: dict) -> str:
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
        if name in {"create_text_file", "make_docx", "make_csv", "make_markdown", "make_website", "report", "make_pdf"}:
            return "create the requested file in the workspace"
        if name in {"read_file", "workspace", "search_files", "list_files"}:
            return "read or search workspace files"
        if name == "web_search":
            return "send this search to the configured web search service"
        if name in {"system_execute", "shell", "run_code"}:
            return "run a command on the Hsx host"
        if name in {"camera_snapshot", "vision_capture", "camera_record"}:
            return "access or capture from the camera"
        if name in {"voice_input", "microphone_start"}:
            return "capture microphone input"
        if name in {"capabilities", "system_info", "disk_usage", "network_status", "list_usb", "list_serial_ports"}:
            return "check live device or system information"
        return action.replace("_", " ") or f"run {name.replace('_', ' ')} for this task"

    @staticmethod
    def _consequence(name: str, action: str) -> str:
        consequences = {
            "delete_owner_files": "Possible consequence: selected files may be permanently removed and may not be recoverable.",
            "overwrite_owner_files": "Possible consequence: existing content may be replaced.",
            "sudo": "Possible consequence: an elevated operation can change system-wide settings.",
            "system_services": "Possible consequence: changing a service can interrupt Hsx or other host services.",
            "shutdown_reboot": "Possible consequence: the host may shut down or restart and interrupt active work.",
            "camera_start": (
                "Possible consequence: the HP camera feed is available in this session and automatically stops after five minutes."
            ),
            "microphone_start": "Possible consequence: the HP microphone opens for this session; audio is discarded and not recorded or sent.",
            "install_packages": "Possible consequence: a pinned package is downloaded into Hsx's project environment.",
            "external_services": "Possible consequence: request data needed for this action may be sent to an external service.",
            "firmware_flash": "Possible consequence: writing firmware can erase or replace a device's current program.",
            "read_secrets": "This action is blocked; sensitive files and credentials cannot be exposed.",
            "pipe_to_shell": "This action is blocked; downloaded or piped scripts cannot be executed.",
        }
        if action in consequences:
            return consequences[action]
        if name in {"read_file", "workspace", "search_files", "list_files"}:
            return "Possible consequence: matching local file contents may be read and included in this task."
        if name == "web_search":
            return "Possible consequence: the search query is sent to the configured web search service."
        if name in {"system_execute", "shell", "run_code"}:
            return "Possible consequence: code or a command will run on the Hsx host with the service account's permissions."
        if name in {"create_text_file", "make_docx", "make_csv", "make_markdown", "make_website", "report", "make_pdf"}:
            return "Possible consequence: a new deliverable will be created in the shared workspace."
        if name in {"capabilities", "system_info", "disk_usage", "network_status", "list_usb", "list_serial_ports"}:
            return "Possible consequence: live local system or device information is read; no settings are changed."
        if name in {"camera_snapshot", "vision_capture", "camera_record"}:
            return "Possible consequence: the camera may capture a frame or recording for this task."
        if name == "microphone_start":
            return "Possible consequence: the HP microphone opens temporarily; input samples are discarded without recording."
        if name == "voice_input":
            return "Possible consequence: microphone audio may be captured for this task."
        return f"Possible consequence: the {name.replace('_', ' ')} tool may read or change information needed for this task."


POLICY = PolicyEngine()
