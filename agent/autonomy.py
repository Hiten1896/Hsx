from __future__ import annotations

import difflib
import base64
import fnmatch
import os
import re
import shlex
from pathlib import Path

import yaml

from config import BASE_DIR, WORKSPACE_DIR, WORK_DIR

PERMISSION_FILE = BASE_DIR / "config" / "permissions.yaml"
_HARD_DENY = re.compile(
    r"(?:\b(?:rm|unlink|truncate|tee|mv|sed\s+-i)\b.{0,100}(?:audit(?:\.jsonl|_log)?|permissions\.ya?ml|agent[/\\]policy\.py)"
    r"|(?:audit(?:\.jsonl|_log)?|permissions\.ya?ml|agent[/\\]policy\.py).{0,100}\b(?:rm|unlink|truncate|tee|mv|sed\s+-i|disable|remove|delete)\b)"
    r"|\b(?:echo|printf|printenv)\b.{0,100}(?:API[_-]?KEY|BEARER[_-]?TOKEN|SECRET)"
    r"|(?:\.env|api[_-]?key|bearer[_-]?token).{0,100}(?:\bcurl\b|\bwget\b|\bnc\b|\bsocat\b|\bssh\b)",
    re.IGNORECASE | re.DOTALL,
)
_SECRET_READ = re.compile(
    r"(?:\.env\b|[\\/]\.ssh[\\/]|[\\/]secrets?[\\/]|"
    r"\bprintenv\b|\benv\b|owner\.pin)",
    re.IGNORECASE,
)
_PIPE_TO_SHELL = re.compile(r"\|\s*(?:sudo\s+)?(?:sh|bash|zsh)\b", re.IGNORECASE)
_PRIVILEGED_COMMANDS = re.compile(r"\b(?:sudo|su|reboot|shutdown|poweroff|halt)\b", re.IGNORECASE)
_DELETIONS = re.compile(r"\b(?:rm|rmdir|unlink|del|erase)\b", re.IGNORECASE)
_WRITES = re.compile(r"\b(?:mv|cp|tee|truncate|sed\s+-i|>|>>|write_file)\b", re.IGNORECASE)
_BASE64_VALUE = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{4,}(?![A-Za-z0-9+/=_-])")


def _inspect_command(command: str) -> str:
    expanded = os.path.expandvars(command)
    inspected = [command, expanded]
    if "base64" in expanded.casefold():
        for encoded in _BASE64_VALUE.findall(expanded):
            try:
                decoded = base64.b64decode(encoded, altchars=b"-_", validate=True).decode("utf-8", "ignore")
            except (ValueError, UnicodeError):
                continue
            if decoded:
                inspected.append(decoded)
    return "\n".join(inspected)


def _within(path: Path, roots: tuple[Path, ...]) -> bool:
    try:
        resolved = path.expanduser().resolve(strict=False)
        return any(resolved == root.resolve() or root.resolve() in resolved.parents for root in roots)
    except (OSError, RuntimeError):
        return False


def permission_rules(path: Path | None = None) -> dict:
    source = path or PERMISSION_FILE
    try:
        value = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("permissions.yaml must contain a mapping.")
    ask_first = value.get("ask_first", [])
    always_allow = value.get("always_allow", [])
    if not isinstance(ask_first, list) or not all(isinstance(item, str) for item in ask_first):
        raise ValueError("permissions.yaml ask_first must be a list of action names.")
    if not isinstance(always_allow, list) or not all(isinstance(item, str) for item in always_allow):
        raise ValueError("permissions.yaml always_allow must be a list of action patterns.")
    return {"ask_first": ask_first, "always_allow": always_allow}


def needs_permission(action: str, *, path: Path | None = None) -> bool:
    rules = permission_rules(path)
    return any(fnmatch.fnmatchcase(action, rule) for rule in rules["ask_first"])


def _write_rules(target: Path, rules: dict) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(rules, sort_keys=False), encoding="utf-8")
    return rules


def set_permission_action(action: str, enabled: bool, *, path: Path | None = None) -> dict:
    target = path or PERMISSION_FILE
    rules = permission_rules(target)
    if enabled and action not in rules["ask_first"]:
        rules["ask_first"].append(action)
    if not enabled:
        rules["ask_first"] = [item for item in rules["ask_first"] if item != action]
    return _write_rules(target, rules)


def add_always_allow(pattern: str, *, path: Path | None = None) -> dict:
    target = path or PERMISSION_FILE
    rules = permission_rules(target)
    if pattern not in rules["always_allow"]:
        rules["always_allow"].append(pattern)
    return _write_rules(target, rules)


def remove_always_allow(pattern: str, *, path: Path | None = None) -> dict:
    target = path or PERMISSION_FILE
    rules = permission_rules(target)
    rules["always_allow"] = [item for item in rules["always_allow"] if item != pattern]
    return _write_rules(target, rules)


def classify_action(tool_name: str, arguments: dict) -> str:
    name = tool_name.casefold()
    action = str(arguments.get("action", "")).casefold()
    if name in {"read_file", "search_files", "list_files", "workspace"}:
        target = "\n".join(
            str(arguments.get(key, ""))
            for key in ("path", "file_path", "filename", "directory")
        )
        if _SECRET_READ.search(target):
            return "read_secrets"
    if name in {"system_execute", "shell"}:
        if name == "shell" and not action:
            action = "exec"
        if action == "exec":
            command = str(arguments.get("command", ""))
            inspected_command = _inspect_command(command)
            lowered = inspected_command.casefold()
            try:
                tokens = shlex.split(command, posix=os.name != "nt")
            except ValueError:
                tokens = [command]
            normalized_tokens = [
                token.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".exe").casefold()
                for token in tokens
            ]
            executable = normalized_tokens[0] if normalized_tokens else ""
            flash_operations = {"write-flash", "write_flash", "write-flash-data", "erase-flash", "erase_flash", "erase-region"}
            if "esptool" in normalized_tokens and flash_operations.intersection(normalized_tokens):
                return "firmware_flash"
            if _SECRET_READ.search(lowered):
                return "read_secrets"
            if _PIPE_TO_SHELL.search(inspected_command):
                return "pipe_to_shell"
            if _PRIVILEGED_COMMANDS.search(inspected_command):
                return "sudo" if re.search(r"\b(?:sudo|su)\b", inspected_command, re.I) else "shutdown_reboot"
            if _DELETIONS.search(inspected_command):
                return "" if _shell_targets_hsx_work(command, arguments) else "delete_owner_files"
            if _WRITES.search(inspected_command):
                return "" if _shell_targets_hsx_work(command, arguments) else "overwrite_owner_files"
            if executable == "systemctl" or re.search(r"\b(?:service|systemctl)\b", command, re.I):
                return "system_services"
            return ""
        if action == "read_file" and _SECRET_READ.search(str(arguments.get("path", ""))):
            return "read_secrets"
        if action == "write_file":
            raw_path = str(arguments.get("path", ""))
            candidate = Path(raw_path) if Path(raw_path).is_absolute() else WORKSPACE_DIR / raw_path
            if not _within(candidate, (WORK_DIR,)):
                return "overwrite_owner_files" if candidate.exists() else "system_services"
        if action == "service_action":
            return "system_services"
    if name in {"camera_snapshot", "vision_capture", "camera_record"}:
        return "camera_start"
    if name in {"voice_input", "microphone_start"}:
        return "microphone_start"
    if name == "capability_resolver":
        return "install_packages"
    if name == "web_search":
        return ""
    if name in {"web_fetch", "music_search", "music_play"}:
        return "external_services"
    if name in {"create_text_file", "make_docx", "make_csv", "make_markdown", "make_website", "report", "make_pdf"}:
        filename = str(arguments.get("filename", arguments.get("path", "")))
        candidate = Path(filename) if Path(filename).is_absolute() else WORK_DIR / filename
        if not _within(candidate, (WORK_DIR,)):
            return "overwrite_owner_files" if candidate.exists() else "system_services"
    return ""


def _shell_targets_hsx_work(command: str, arguments: dict) -> bool:
    """Only exempt simple, explicit mutations wholly contained in Hsx Work."""
    try:
        tokens = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return False
    if not tokens:
        return False

    executable = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".exe").casefold()
    if executable in {"rm", "rmdir", "unlink", "del", "erase"}:
        targets = [
            token for token in tokens[1:]
            if not token.startswith("-") and token != "--"
        ]
    elif executable in {"mv", "cp"}:
        targets = [tokens[-1]] if len(tokens) >= 3 else []
    elif executable == "tee":
        targets = [token for token in tokens[1:] if not token.startswith("-")]
    elif executable == "truncate":
        targets = [
            token.split("=", 1)[1]
            for token in tokens[1:]
            if token.startswith("--") and token.split("=", 1)[0] in {"--size", "--reference"}
        ]
    else:
        return False

    if not targets or any(
        any(character in target for character in ";|><$`()")
        for target in targets
    ):
        return False
    working_directory = Path(str(arguments.get("cwd") or WORKSPACE_DIR)).expanduser()
    return all(
        _within(
            Path(target.strip("\"'"))
            if Path(target.strip("\"'")).is_absolute()
            else working_directory / target.strip("\"'"),
            (WORK_DIR,),
        )
        for target in targets
    )


def is_hard_denied(tool_name: str, arguments: dict) -> bool:
    if tool_name in {"POLICY", "disable_audit", "remove_permission_system"}:
        return True
    raw = "\n".join(str(arguments.get(key, "")) for key in ("command", "path", "content"))
    combined = _inspect_command(raw)
    if _HARD_DENY.search(combined):
        return True
    lowered = combined.casefold()
    return any(key in lowered for key in ("api_key", "api-key", "bearer token", "authorization:")) and any(
        sink in lowered for sink in ("curl", "wget", "http", "https", "printenv", "echo")
    )


def parse_approval_answer(answer: str) -> bool | None:
    choice = parse_approval_choice(answer)
    return None if choice is None else choice != "reject"


def parse_approval_choice(answer: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9 ]+", " ", answer.casefold())
    normalized = " ".join(normalized.split())
    if not normalized:
        return None
    choices = {
        "accept once": "once", "allow": "once", "yes": "once", "y": "once", "ok": "once", "proceed": "once", "approve": "once", "i understand proceed": "once",
        "reject": "reject", "deny": "reject", "no": "reject", "n": "reject", "cancel": "reject", "stop": "reject", "not now": "reject",
    }
    if normalized in choices:
        return choices[normalized]
    for candidate in ("accept once", "reject", "proceed", "cancel"):
        if difflib.SequenceMatcher(None, normalized, candidate).ratio() >= 0.8:
            return {"accept once": "once", "reject": "reject", "proceed": "once", "cancel": "reject"}[candidate]
    return None
