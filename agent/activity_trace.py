from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True)
class ToolActivity:
    icon: str
    present: str
    past: str


ACTIVITY_MAP = {
    "audio_play": ToolActivity("music", "Playing", "Played"),
    "ask_user": ToolActivity("question", "Asking", "Asked"),
    "camera_record": ToolActivity("camera", "Recording", "Recorded"),
    "capabilities": ToolActivity("gear", "Checking", "Checked"),
    "capability_resolver": ToolActivity("package", "Preparing", "Prepared"),
    "compress_files": ToolActivity("package", "Building", "Built"),
    "create_dynamic_tool": ToolActivity("gear", "Creating", "Created"),
    "create_text_file": ToolActivity("pencil", "Editing", "Edited"),
    "describe_image": ToolActivity("images", "Viewing", "Viewed"),
    "disk_usage": ToolActivity("gear", "Checking", "Checked"),
    "download_media": ToolActivity("download", "Downloading", "Downloaded"),
    "forget": ToolActivity("save", "Removing note", "Removed note"),
    "git_ops": ToolActivity("terminal", "Running", "Ran"),
    "list_files": ToolActivity("list", "Listing", "Listed"),
    "list_serial_ports": ToolActivity("list", "Listing", "Listed"),
    "list_usb": ToolActivity("list", "Listing", "Listed"),
    "make_csv": ToolActivity("package", "Building", "Built"),
    "make_docx": ToolActivity("package", "Building", "Built"),
    "make_markdown": ToolActivity("pencil", "Editing", "Edited"),
    "make_website": ToolActivity("pencil", "Editing", "Edited"),
    "music_play": ToolActivity("music", "Preparing", "Prepared"),
    "music_search": ToolActivity("music", "Searching", "Searched"),
    "network_status": ToolActivity("gear", "Checking", "Checked"),
    "notes": ToolActivity("save", "Saving", "Saved"),
    "read_file": ToolActivity("book", "Reading", "Read"),
    "recall": ToolActivity("save", "Reading note", "Read note"),
    "remember": ToolActivity("save", "Saving", "Saved"),
    "report": ToolActivity("package", "Building", "Built"),
    "run_code": ToolActivity("terminal", "Running", "Ran"),
    "search_files": ToolActivity("search", "Searching", "Searched"),
    "shell": ToolActivity("terminal", "Running", "Ran"),
    "system_execute": ToolActivity("terminal", "Running", "Ran"),
    "system_info": ToolActivity("gear", "Checking", "Checked"),
    "system_status": ToolActivity("gear", "Checking", "Checked"),
    "task_plan": ToolActivity("brain", "Planning", "Planned"),
    "tool_creator": ToolActivity("gear", "Creating", "Created"),
    "video_edit": ToolActivity("pencil", "Editing", "Edited"),
    "vision_capture": ToolActivity("camera", "Capturing", "Captured"),
    "voice_input": ToolActivity("gear", "Processing", "Processed"),
    "web_fetch": ToolActivity("globe", "Fetching", "Fetched"),
    "web_search": ToolActivity("search", "Searching", "Searched"),
    "workspace": ToolActivity("book", "Reading", "Read"),
}

DEFAULT_ACTIVITY = ToolActivity("gear", "Working", "Completed")
IMAGE_SUFFIXES = {".avif", ".bmp", ".gif", ".heic", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_ -]?key|access[_ -]?token|auth(?:orization)?|password|secret|token)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_LONG_SECRET = re.compile(r"\b[A-Za-z0-9_-]{40,}\b")


def redact_activity_text(value: str, limit: int = 2000) -> str:
    text = _BEARER.sub("Bearer [REDACTED]", str(value))
    text = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", text)
    text = _LONG_SECRET.sub("[REDACTED]", text)
    return text[:limit]


def redact_activity_value(value: Any, *, limit: int = 2000) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, child in value.items():
            if re.search(r"(?i)(api.?key|token|password|secret|authorization|credential|content|file_data)", str(key)):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = redact_activity_value(child, limit=limit)
        return redacted
    if isinstance(value, list):
        return [redact_activity_value(item, limit=limit) for item in value[:50]]
    if isinstance(value, str):
        return redact_activity_text(value, limit)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_activity_text(str(value), limit)


def _file_path(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    suffix = value.replace("\\", "/").rsplit("/", 1)[-1]
    return redact_activity_text(suffix, 180) if suffix else ""


def _target(tool_name: str, arguments: dict) -> tuple[str, str]:
    activity = ACTIVITY_MAP.get(tool_name, DEFAULT_ACTIVITY)
    images = arguments.get("images") or arguments.get("paths")
    if tool_name == "describe_image" and isinstance(images, list):
        count = len(images)
        if count > 1:
            return f"{count} images", ""
    for key in ("command", "path", "filename", "file", "url", "query", "package", "name"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            text = redact_activity_text(value.strip(), 240)
            if key == "path" or key == "filename" or key == "file":
                return _file_path(text), text
            return text, ""
    if tool_name == "describe_image":
        return "image", ""
    if tool_name == "vision_capture":
        return "camera snapshot", ""
    return activity.past.casefold(), ""


def tool_start_event(tool_name: str, arguments: dict, call_id: str) -> dict:
    activity = ACTIVITY_MAP.get(tool_name, DEFAULT_ACTIVITY)
    if tool_name == "report" and "style" in str(arguments).casefold():
        activity = ToolActivity("palette", "Extracting style", "Extracted style")
    target, file_path = _target(tool_name, arguments)
    if tool_name == "describe_image" and target == "image":
        target = "1 image"
    return {
        "tool_call_id": redact_activity_text(call_id, 120),
        "tool": redact_activity_text(tool_name, 120),
        "icon": activity.icon,
        "present": activity.present,
        "past": activity.past,
        "target": target,
        "file_path": file_path,
        "arguments": redact_activity_value(arguments),
        "status": "running",
    }


def _result_paths(result: Any) -> list[str]:
    if not isinstance(result, dict):
        return []
    values = []
    for key in ("path", "output_path", "file", "filename"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    for key in ("paths", "images", "files"):
        value = result.get(key)
        if isinstance(value, list):
            values.extend(item for item in value if isinstance(item, str) and item.strip())
    return values


def _bounded_output(value: Any, *, max_lines: int = 2000) -> list[str]:
    if not isinstance(value, str) or not value:
        return []
    lines = value.splitlines()
    return [redact_activity_text(line, 500) for line in lines[:max_lines]]


def tool_end_event(start: dict, result: Any, duration_ms: float, *, failed: bool = False) -> dict:
    paths = _result_paths(result)
    image_paths = list(paths)
    if start.get("tool") == "describe_image" and start.get("file_path"):
        image_paths.append(start["file_path"])
    images = [
        redact_activity_text(path, 500)
        for path in image_paths
        if "." in path.replace("\\", "/").rsplit("/", 1)[-1]
        and f".{path.replace(chr(92), '/').rsplit('.', 1)[-1].casefold()}" in IMAGE_SUFFIXES
    ]
    file_path = start.get("file_path") or (paths[0] if paths else "")
    target = start.get("target", "")
    if file_path and paths:
        target = _file_path(paths[0])
    summary = ""
    if isinstance(result, dict):
        if result.get("success") is False or result.get("allowed") is False or failed:
            summary = result.get("error") or result.get("message") or "The action failed."
        elif isinstance(result.get("message"), str):
            summary = result["message"]
        elif isinstance(result.get("files"), list):
            summary = f"Found {len(result['files'])} files."
        elif isinstance(result.get("results"), list):
            summary = f"Found {len(result['results'])} results."
        elif isinstance(result.get("content"), str):
            summary = f"Read {len(result['content'])} characters."
        elif result.get("success") is True:
            summary = "Completed successfully."
    elif isinstance(result, str):
        summary = result
    arguments = start.get("arguments") if isinstance(start.get("arguments"), dict) else {}
    stdout = result.get("stdout", "") if isinstance(result, dict) else ""
    stderr = result.get("stderr", "") if isinstance(result, dict) else ""
    output_lines = _bounded_output("\n".join(filter(None, (str(stdout), str(stderr)))))
    command = arguments.get("command") or arguments.get("code") or ""
    exit_status = None
    if isinstance(result, dict):
        exit_status = result.get("exit_code", result.get("returncode"))
    event = {
        **start,
        "target": redact_activity_text(str(target), 240),
        "file_path": redact_activity_text(str(file_path), 500),
        "duration_ms": max(0, round(duration_ms)),
        "status": "failed" if failed or (isinstance(result, dict) and result.get("success") is False) else "completed",
        "output": redact_activity_value(summary, limit=1000),
        "details": {
            "command": redact_activity_text(str(command), 2000),
            "exit_status": exit_status,
            "output_lines": output_lines,
            "arguments": redact_activity_value(arguments),
        },
        "images": images[:20],
    }
    if isinstance(result, dict) and result.get("artifact") is True and paths:
        event["artifact"] = True
    if start.get("tool") == "capabilities" and isinstance(result, dict):
        event["capabilities"] = redact_activity_value(
            {"groups": result.get("groups", []), "cannot_do": result.get("cannot_do", [])}
        )
    if start.get("tool") == "music_play" and isinstance(result, dict):
        player = result.get("browser_action")
        if isinstance(player, dict) and player.get("type") == "music_play":
            event["music_player"] = redact_activity_value(player)
    return event


def format_duration(duration_ms: int | float) -> str:
    seconds = max(0, round(float(duration_ms) / 1000))
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m {seconds % 60}s"
