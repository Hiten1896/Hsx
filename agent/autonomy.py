from __future__ import annotations

import difflib
import os
import re
import shlex
from pathlib import Path

from config import WORKSPACE_DIR, WORK_DIR

_YES = ("yes", "y", "ok", "proceed", "approve", "i understand", "i understand proceed")
_NO = ("no", "n", "cancel", "stop")
_SAFE_TOOLS = {
    "ask_user", "compress_files", "create_text_file", "disk_usage", "list_files",
    "list_usb", "make_csv", "make_docx", "make_markdown", "make_website",
    "network_status", "read_file", "recall", "report", "search_files",
    "system_info", "system_status", "workspace",
}
_NORMAL_TOOLS = {
    "download_media", "git_ops", "notes", "remember", "run_code", "shell",
    "tool_creator", "video_edit", "web_fetch", "web_search",
}
_BLOCKED = re.compile(
    r"(?:audit(?:\.jsonl|_log)?|approval(?:_system|\.py)?|owner\.pin)"
    r".{0,120}(?:\b(?:rm|unlink|delete|truncate|disable|clear|overwrite|write|open)\b|[>|])"
    r"|(?:\b(?:rm|unlink|delete|truncate|disable|clear|overwrite|write|open)\b|[>|])"
    r".{0,80}(?:audit(?:\.jsonl|_log)?|approval(?:_system|\.py)?|owner\.pin)",
    re.IGNORECASE | re.DOTALL,
)
_SECRET_SEND = re.compile(
    r"(?:\.env|(?:api[_-]?key|token|password)|\bprintenv\b|\benv\b)"
    r".{0,160}(?:\bcurl\b|\bwget\b|\bnc\b|\bsocat\b|\bssh\b)",
    re.IGNORECASE | re.DOTALL,
)
_SECRET_READ = re.compile(r"(?:\.env\b|/\.ssh/|\bprintenv\b|\benv\b|owner\.pin)", re.IGNORECASE)
_SECRET_EXPOSE = re.compile(
    r"\b(?:echo|printf|printenv)\b.{0,100}(?:api[_-]?key|token|password|secret|\.env)",
    re.IGNORECASE | re.DOTALL,
)
_DANGEROUS_COMMANDS = {
    "chmod", "chown", "cp", "dd", "fdisk", "firewall-cmd", "iptables", "mkfs",
    "mount", "mv", "passwd", "reboot", "rm", "rmdir", "shutdown", "su", "sudo",
    "systemctl", "tailscale", "tee", "truncate", "useradd", "usermod", "visudo",
}
_SAFE_COMMANDS = {
    "cat", "date", "df", "du", "free", "getent", "grep", "head", "hostname",
    "ip", "ls", "lsusb", "lscpu", "netstat", "printf", "ps", "pwd", "sed",
    "ss", "tail", "uname", "uptime", "whoami", "which",
}


def autonomy_mode() -> str:
    mode = os.getenv("HSX_AUTONOMY", "auto").strip().lower() or "auto"
    if mode not in {"auto", "ask", "strict"}:
        raise ValueError("HSX_AUTONOMY must be auto, ask, or strict.")
    return mode


def parse_approval_answer(answer: str) -> bool | None:
    normalized = re.sub(r"[^a-z0-9 ]+", " ", answer.casefold())
    normalized = " ".join(normalized.split())
    if not normalized:
        return None
    for candidate in _YES:
        if normalized == candidate or difflib.SequenceMatcher(None, normalized, candidate).ratio() >= 0.88:
            return True
    for candidate in _NO:
        if normalized == candidate or difflib.SequenceMatcher(None, normalized, candidate).ratio() >= 0.8:
            return False
    return None


def _within(path: Path, roots: tuple[Path, ...]) -> bool:
    try:
        resolved = path.expanduser().resolve(strict=False)
        return any(resolved == root.resolve() or root.resolve() in resolved.parents for root in roots)
    except (OSError, RuntimeError):
        return False


def _command_tier(command: str) -> str:
    lowered = command.casefold().strip()
    if _BLOCKED.search(lowered) or _SECRET_SEND.search(lowered) or _SECRET_EXPOSE.search(lowered):
        return "BLOCKED"
    if _SECRET_READ.search(lowered):
        return "DANGEROUS"
    if re.search(r"\b(?:curl|wget)\b[^|;&]*\|\s*(?:sudo\s+)?(?:sh|bash|zsh)\b", lowered):
        return "DANGEROUS"
    try:
        tokens = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return "DANGEROUS"
    if not tokens:
        return "DANGEROUS"
    if any(token in {";", "&&", "||", "|", ">", ">>", "<", "<<"} for token in tokens):
        return "DANGEROUS"
    if any("$" in token or "`" in token for token in tokens):
        return "DANGEROUS"
    executable_index = next(
        (index for index, token in enumerate(tokens) if "=" not in token or token.startswith("-")),
        0,
    )
    executable = Path(tokens[executable_index]).name.casefold()
    args = [token.casefold() for token in tokens[executable_index + 1 :]]
    if executable in _DANGEROUS_COMMANDS:
        if executable == "systemctl" and args[:2] == ["restart", "hsx"]:
            return "NORMAL"
        return "DANGEROUS"
    if executable == "sed" and any(argument == "-i" or argument.startswith("--in-place") for argument in args):
        return "DANGEROUS"
    if executable in {"curl", "wget"}:
        return "NORMAL"
    if executable in {"pip", "pip3", "uv"} and any(value in {"install", "add"} for value in args):
        return "NORMAL"
    if executable in _SAFE_COMMANDS:
        return "SAFE"
    return "DANGEROUS"


def _path_tier(action: str, raw_path: str, arguments: dict) -> str:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = WORKSPACE_DIR / path
    normalized = str(path).replace("\\", "/").casefold()
    if Path(raw_path).name.casefold() == ".env" or any(
        marker in normalized for marker in ("/.ssh/", "/secrets/", "/etc/", "/owner.pin")
    ):
        return "DANGEROUS"
    if action == "write_file":
        if not _within(path, (WORKSPACE_DIR, WORK_DIR)):
            return "DANGEROUS"
        if path.exists():
            return "DANGEROUS"
        return "SAFE"
    if _within(path, (WORKSPACE_DIR, WORK_DIR)) or normalized.startswith("/var/log/"):
        return "SAFE"
    return "DANGEROUS"


def classify_action(tool_name: str, arguments: dict, base_risk: str | None = None) -> dict[str, str]:
    name = tool_name.strip().lower()
    action = str(arguments.get("action", "")).strip().lower()
    if name == "system_execute":
        if action == "exec":
            tier = _command_tier(str(arguments.get("command", "")))
            command = " ".join(str(arguments.get("command", "")).split())[:160]
            command = re.sub(
                r"(?i)(api[_-]?key|token|password|secret)(\s*[=:]\s*)[^\s,;]+",
                r"\1\2[REDACTED]",
                command,
            )
            command = re.sub(r"(?i)(bearer\s+)[^\s,\"']+", r"\1[REDACTED]", command)
            summary = f"run the command {command!r}" if command else "run a system command"
        elif action in {"read_file", "write_file", "list_dir", "stat"}:
            path = str(arguments.get("path", ""))
            tier = _path_tier(action, path, arguments)
            summary = f"{action.replace('_', ' ')} {path or 'a host path'}"
        elif action == "resource" or action in {"processes", "services"}:
            tier, summary = "SAFE", f"inspect local {action}"
        elif action == "service_action":
            service = str(arguments.get("service", "")).strip().lower()
            service_action = str(arguments.get("service_action", "")).strip().lower()
            if service == "hsx" and service_action == "restart":
                tier = "NORMAL"
            else:
                tier = "DANGEROUS"
            summary = f"{service_action or 'manage'} the {service or 'host'} service"
        else:
            tier, summary = "DANGEROUS", f"run system action {action or 'unknown'}"
    elif name == "create_text_file":
        path = str(arguments.get("path", ""))
        tier = "SAFE" if path and _within(Path(path) if Path(path).is_absolute() else WORKSPACE_DIR / path, (WORKSPACE_DIR, WORK_DIR)) else "BLOCKED"
        summary = f"create {path or 'a workspace file'}"
    elif name in _SAFE_TOOLS:
        tier, summary = "SAFE", f"use {name.replace('_', ' ')}"
    elif name in _NORMAL_TOOLS:
        tier, summary = "NORMAL", f"use {name.replace('_', ' ')}"
    else:
        metadata_risk = str(base_risk or "").lower()
        tier = {"safe": "SAFE", "confirm": "NORMAL", "dangerous": "DANGEROUS"}.get(metadata_risk, "DANGEROUS")
        summary = f"run {name.replace('_', ' ')}"

    if tier == "BLOCKED":
        why = "This action is blocked by the host safety policy."
    elif tier == "DANGEROUS":
        why = "This action can make an irreversible or security-sensitive change."
    elif tier == "NORMAL":
        why = "This action changes files, software, or uses an external service."
    else:
        why = "This is a read-only or workspace-safe action."

    details = str(arguments.get("command", "")) if arguments.get("command") else ""
    if not details and arguments.get("path"):
        details = f"path: {arguments['path']}"
    details = re.sub(r"(?i)(api[_-]?key|token|password|secret)(\s*[=:]\s*)[^\s,;]+", r"\1\2[REDACTED]", details)[:500]
    return {"tier": tier, "summary": summary, "why": why, "details": details}
