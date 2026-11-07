import base64
import binascii
import difflib
import errno
import hmac
import io
import json
import logging
import mimetypes
import os
import queue
import re
import shutil
import sqlite3
import sys
import threading
import time
import copy
import uuid
from collections.abc import Callable
from http.cookies import SimpleCookie
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from api.chat import ChatStreamRegistry
from api.devices import resolve_target_device
from api.files import resolve_output
from api.jobs import ask_job_payload
from agent.artifacts import ArtifactStore
from agent.activity_trace import redact_activity_text, redact_activity_value, tool_end_event
from agent.ask_user import ApprovalRequest, AskUserJobs, AskUserRequest
from agent.capabilities import run as _capabilities
from agent.jobs import JobQueue
from agent.approval import ApprovalGuard
from agent.devices import DeviceRegistry, system_body_state
from agent.feedback import FeedbackStore
from agent.conversations import ConversationStore
from agent.hardware import AUDIO_ALERTS, CAMERA, MICROPHONE, MOTION_MONITOR
from agent.hardware.camera import MAX_FRAME_AGE_SECONDS, camera_session_owner
from agent.hardware.microphone import microphone_session_owner
from agent.llm.router import default_router
from agent.llm.types import LLMRequest
from agent.llm_client import PROVIDER_BANDIT, call_llm, record_provider_feedback
from agent.llm.providers import GroqProvider, ProviderError
from agent.ml.anomaly import ANOMALY_MONITOR, SystemMetrics
from agent.ml.forecaster import FORECASTER, classify_job
from agent.ml.dataset_exporter import SFT_DATASET
from agent.ml.intent_learning import UNCERTAIN_INTENTS
from agent.ml.log_recorder import LOG_RECORDER
from agent.mesh.node_gateway import MeshGateway
from agent.ml.memory import EXPERIENCE_MEMORY
from agent.memory import MemoryManager
from agent.music_catalog import MUSIC_CATALOG
from agent.notifier import NOTIFIER
from agent.orchestrator import _vision_request, run_agent
from agent.policy import POLICY
from agent.router import INTENT_ROUTER, LLM_TELEMETRY, ROUTER_LOGGER
from agent.self_improvement import SelfImprovement
from agent.system_tools import system_info
from agent.skill_loader import load_skills, set_runtime_registry
from agent.tools.dynamic_registry import DYNAMIC_TOOLS, register_meta_tool
from agent.workspace import Workspace, task_output_scope, workspace_search
from config import (
    DATA_DIR,
    ALLOWED_ORIGINS,
    AUTH_TOKEN,
    HOST,
    GROQ_API_KEY,
    GROQ_MODEL,
    JOBS_DB,
    MEMORY_DB,
    MODE,
    PORT,
    UPLOADS_DIR,
    WORKSPACE_DIR,
    WORK_DIR,
)
from config import BASE_DIR, IMPROVEMENTS_DB
from video_tools.pipeline import create_video_job, get_video_job, list_video_jobs

_ask_job_payload = ask_job_payload

ROOT = Path(__file__).resolve().parent
LOGGER = logging.getLogger("hsx.server")
ARTIFACTS = ArtifactStore(DATA_DIR / "artifacts.sqlite")
ARTIFACT_SUFFIXES = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".md", ".txt", ".html",
    ".htm", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".py",
    ".js", ".jsx", ".ts", ".tsx", ".css", ".json", ".yaml", ".yml",
    ".toml", ".rs", ".go", ".java", ".c", ".h", ".cpp", ".cs", ".sh",
    ".zip",
}
TOOLS, SKILL_MAP = load_skills(str(ROOT / "skills"))
try:
    DYNAMIC_TOOLS.restore(TOOLS, SKILL_MAP)
    register_meta_tool(TOOLS, SKILL_MAP)
except (OSError, RuntimeError, TypeError, ValueError):
    LOGGER.exception("Could not restore the constrained dynamic tool registry.")
TOOLS.append({
    "name": "capabilities",
    "description": "Report Hsx's registered tools, live provider and device setup checks, and known gaps.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
})
SKILL_MAP["capabilities"] = _capabilities
if INTENT_ROUTER.model_path.is_file():
    INTENT_ROUTER.load()
else:
    INTENT_ROUTER.train_from_tools(TOOLS)
set_runtime_registry(TOOLS, SKILL_MAP)
HISTORY = []
SESSION_HISTORIES = {}
SESSION_LAST_JOBS = {}
SESSION_LOCKS = {}
SESSION_LOCKS_GUARD = threading.Lock()
RUN_CANCEL_EVENTS: dict[str, threading.Event] = {}
CHAT_STREAMS = ChatStreamRegistry()
MEMORY = MemoryManager(MEMORY_DB)
JOBS = JobQueue(JOBS_DB)
ASK_JOBS = AskUserJobs(JOBS_DB)
ASK_JOBS.start_expiration_worker()
APPROVAL_GUARD = ApprovalGuard(
    Path.home() / ".hsx" / "owner.pin",
    os.getenv("HSX_OWNER_PIN"),
)
POLICY.approval_guard = APPROVAL_GUARD
IMPROVEMENTS = SelfImprovement(
    IMPROVEMENTS_DB,
    approval_guard=APPROVAL_GUARD,
)
DEVICES = DeviceRegistry(approval_guard=APPROVAL_GUARD)
WORKSPACE = Workspace(WORKSPACE_DIR, WORK_DIR)
FEEDBACK_STORE = FeedbackStore(DATA_DIR / "feedback.db")
CONVERSATIONS = ConversationStore(DATA_DIR / "conversations.db")
_RESOURCE_ALERT_LOCK = threading.Lock()
_RESOURCE_ALERT_STATE = {"cpu": False, "memory": False, "disk": False}
_ACTIVITY_IMAGE_LOCK = threading.Lock()
_ACTIVITY_IMAGE_PATHS: dict[str, Path] = {}
_ACTIVITY_THUMBNAILS: dict[tuple[str, int, int], bytes] = {}
_ACTIVITY_RETRY_LOCK = threading.Lock()
_ACTIVITY_RETRY_CALLS: dict[str, dict] = {}
_ACTIVITY_FILE_TOOLS = {
    "compress_files", "create_text_file", "make_csv", "make_docx", "make_markdown",
    "make_website", "report", "run_code", "shell", "system_execute", "video_edit",
}
MESH_GATEWAY = MeshGateway.from_environment()
def _hardware_audio_alert(*, succeeded: bool, message: str = "") -> None:
    try:
        if succeeded:
            AUDIO_ALERTS.notify_success(message or "Hsx task completed successfully.")
        else:
            AUDIO_ALERTS.play_async("warning")
    except (OSError, RuntimeError, ValueError):
        LOGGER.exception("Could not queue a hardware audio alert.")


_FAILED_RUN_IDS: set[str] = set()


def _emit_chat_event(job_id: str, event: str, payload: dict) -> None:
    if event == "error":
        if job_id in _FAILED_RUN_IDS:
            return
        _FAILED_RUN_IDS.add(job_id)
    CHAT_STREAMS.emit(job_id, event, payload)
    if event == "done":
        RUN_CANCEL_EVENTS.pop(job_id, None)


def _activity_resolve_path(raw_path: str) -> Path | None:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            return None
        allowed_roots = (WORKSPACE.root, DATA_DIR.resolve())
        return resolved if any(resolved == root or root in resolved.parents for root in allowed_roots) else None
    for resolver in (WORKSPACE.resolve_read, WORKSPACE.resolve_write):
        try:
            resolved = resolver(raw_path)
        except (OSError, ValueError):
            continue
        if resolved.is_file():
            return resolved
    return None


def _activity_snapshot(path: Path | None) -> dict | None:
    if path is None or not path.is_file():
        return None
    try:
        size = path.stat().st_size
        if size > 512 * 1024:
            return {"path": path, "binary": True, "size": size, "text": ""}
        data = path.read_bytes()
    except OSError:
        LOGGER.exception("Could not read a file for the activity trace.")
        return None
    try:
        if b"\x00" in data:
            raise UnicodeDecodeError("utf-8", data, 0, 1, "binary content")
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"path": path, "binary": True, "size": len(data), "text": ""}
    return {"path": path, "binary": False, "size": len(data), "text": text}


def _activity_diff(before: dict | None, after: dict | None, path: str) -> dict | None:
    if after is None:
        return None
    before_text = before.get("text", "") if before and not before.get("binary") else ""
    after_text = after.get("text", "")
    if (before and before.get("binary")) or after.get("binary"):
        size = after.get("size", 0)
        return {
            "path": path,
            "added": 0,
            "removed": 0,
            "binary": True,
            "size_bytes": size,
            "hunks": [],
        }
    before_lines = before_text.splitlines()
    after_lines = after_text.splitlines()
    diff_lines = list(difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=path if before else "/dev/null",
        tofile=path,
        n=3,
        lineterm="",
    ))
    hunks = []
    current = None
    old_line = new_line = 0
    total_added = total_removed = total_lines = 0
    for line in diff_lines:
        if line.startswith("@@"):
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if not match:
                continue
            old_line, new_line = map(int, match.groups())
            current = {"header": line[:500], "lines": []}
            hunks.append(current)
            continue
        if line.startswith(("--- ", "+++ ")) or current is None:
            continue
        prefix = line[:1]
        text = redact_activity_text(line[1:] if prefix in {" ", "+", "-"} else line, 2000)
        record = {"kind": {" ": "context", "+": "added", "-": "removed"}.get(prefix, "context"), "text": text}
        if prefix == " ":
            record.update({"old_line": old_line, "new_line": new_line})
            old_line += 1
            new_line += 1
        elif prefix == "-":
            record["old_line"] = old_line
            old_line += 1
        elif prefix == "+":
            record["new_line"] = new_line
            new_line += 1
        total_lines += 1
        if prefix == "+":
            total_added += 1
        elif prefix == "-":
            total_removed += 1
        if total_lines <= 1000:
            current["lines"].append(record)
    return {
        "path": path,
        "added": total_added,
        "removed": total_removed,
        "hunks": hunks,
        "truncated": total_lines > 1000,
    }


def _persist_activity_event(task_dir: str, event: str, payload: dict, job_id: str = "") -> None:
    target_id = job_id or (task_dir if task_dir else "default")
    scratch_dir = DATA_DIR / "scratch" / target_id
    try:
        scratch_dir.mkdir(parents=True, exist_ok=True)
        trace_path = scratch_dir / "activity-trace.jsonl"
        if trace_path.exists() and trace_path.stat().st_size > 1_000_000:
            return
        with trace_path.open("a", encoding="utf-8") as trace:
            trace.write(json.dumps({"event": event, "data": payload}, ensure_ascii=False) + "\n")
    except (OSError, ValueError):
        pass


def _register_activity_images(paths: list[str]) -> list[dict]:
    registered = []
    for raw_path in paths:
        path = _activity_resolve_path(raw_path)
        if path is None:
            continue
        image_id = uuid.uuid4().hex
        with _ACTIVITY_IMAGE_LOCK:
            if len(_ACTIVITY_IMAGE_PATHS) >= 256:
                oldest = next(iter(_ACTIVITY_IMAGE_PATHS))
                _ACTIVITY_IMAGE_PATHS.pop(oldest)
            _ACTIVITY_IMAGE_PATHS[image_id] = path
        try:
            display_path = path.relative_to(WORKSPACE.root).as_posix()
        except ValueError:
            display_path = path.name
        registered.append({"id": image_id, "path": redact_activity_text(display_path, 500)})
    return registered


def _activity_display_path(raw_path: str) -> str:
    path = _activity_resolve_path(raw_path)
    if path is None:
        return ""
    try:
        return path.relative_to(WORKSPACE.root).as_posix()
    except ValueError:
        return ""


def _retry_activity_tool(job_id: str, call_id: str) -> tuple[dict, int]:
    with _ACTIVITY_RETRY_LOCK:
        retry = _ACTIVITY_RETRY_CALLS.get(call_id)
    if not retry or retry.get("job_id") != job_id or time.time() - retry.get("created_at", 0) > 3600:
        return {"error": "This failed step is no longer available to retry."}, 404
    job = ASK_JOBS.get(job_id)
    if job is None or job.get("session_id") != retry["session_id"]:
        return {"error": "This failed step is no longer available to retry."}, 404
    tool_name = retry["tool"]
    tool = next((item for item in TOOLS if item.get("name") == tool_name), None)
    runner = SKILL_MAP.get(tool_name)
    if tool is None or runner is None:
        return {"error": "The original tool is no longer available."}, 404
    arguments = dict(retry["arguments"])
    decision = POLICY.decide(tool, arguments, retry["session_id"])
    if not decision.get("allowed"):
        return {
            "error": (
                "This step needs approval. Submit the request again to use the normal approval flow."
                if decision.get("status") == "waiting_approval"
                else "This step is blocked by the host safety policy."
            ),
            "status": decision.get("status", "blocked"),
        }, 403
    start = retry["start"]
    raw_before = start.get("file_path") or next(
        (arguments.get(key) for key in ("path", "filename", "file") if isinstance(arguments.get(key), str)),
        "",
    )
    before_path = _activity_resolve_path(raw_before) if raw_before else None
    before = _activity_snapshot(before_path)
    ROUTER_LOGGER.log_tool_prediction(retry["session_id"], job_id, tool_name, call_id)
    started = time.perf_counter()
    POLICY.audit_event("activity_step_retry", retry["session_id"], job_id, tool_name)
    try:
        if tool_name in {"audio_play", "camera_record", "music_play"}:
            arguments["target_device"] = retry["target_device"]
        result = runner(**arguments)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        result = {"success": False, "error": str(error)}
    duration_ms = (time.perf_counter() - started) * 1000
    failed = isinstance(result, dict) and result.get("success") is False
    ROUTER_LOGGER.log_tool_execution(
        retry["session_id"], job_id, tool_name, call_id, duration_ms, "failed" if failed else "succeeded"
    )
    activity = tool_end_event(start, result, duration_ms, failed=failed)
    raw_after = next((path for path in _result_paths_for_activity(result)), "") if isinstance(result, dict) else ""
    after_path = _activity_resolve_path(raw_after or raw_before) if raw_after or raw_before else None
    after = _activity_snapshot(after_path)
    if tool_name in _ACTIVITY_FILE_TOOLS and after_path is not None:
        relative_path = _activity_display_path(str(after_path)) or after_path.name
        diff = _activity_diff(before, after, relative_path)
        if diff is not None:
            activity["diff"] = diff
            activity["file_path"] = relative_path
            if before is None and diff["added"]:
                activity.update({"icon": "file-plus", "present": "Creating", "past": "Created"})
    activity["images"] = _register_activity_images(activity.get("images", []))
    safe_activity = redact_activity_value(activity)
    _persist_activity_event(retry.get("task_dir", ""), "tool_end", safe_activity, job_id=retry.get("job_id", ""))
    with _ACTIVITY_RETRY_LOCK:
        if failed:
            retry["created_at"] = time.time()
            _ACTIVITY_RETRY_CALLS[call_id] = retry
        else:
            _ACTIVITY_RETRY_CALLS.pop(call_id, None)
    return {"activity": safe_activity, "status": "failed" if failed else "completed"}, 200


def _result_paths_for_activity(result: dict) -> list[str]:
    values = []
    for key in ("path", "output_path", "file", "filename"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    return values


def _job_error(error: Exception) -> str:
    """Return a useful, bounded error without exposing configured secrets."""
    message = str(error) or error.__class__.__name__
    for name in (
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GROK_API_KEY",
        "XAI_API_KEY",
        "GROQ_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
    ):
        value = os.getenv(name, "")
        if value:
            message = message.replace(value, "***")
    return message[:1000]


def _record_job_telemetry(
    job: dict,
    duration_seconds: float,
    *,
    succeeded: bool,
    sensitive: bool = False,
    response: str = "",
    history: list | None = None,
    error_message: str = "",
) -> None:
    try:
        LOG_RECORDER.record_execution(
            job.get("request", ""),
            response,
            run_id=job.get("id", ""),
            history=history,
            succeeded=succeeded,
            error=error_message,
            sensitive=sensitive,
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        LOGGER.exception("Could not persist DPO execution telemetry for job %s.", job.get("id", ""))
    try:
        if LOG_RECORDER.is_installed:
            LOG_RECORDER.record_event(
                "job_telemetry",
                {
                    "job_id": job.get("id", ""),
                    "duration_seconds": round(duration_seconds, 3),
                    "succeeded": succeeded,
                },
                sensitive=sensitive,
            )
        FORECASTER.record_job(
            classify_job(job["request"]),
            duration_seconds,
            succeeded=succeeded,
        )
        if succeeded and not sensitive:
            EXPERIENCE_MEMORY.record_success(job["request"], response)
            SFT_DATASET.record_success(
                job["request"],
                response,
                job_id=job["id"],
                history=history,
                sensitive=sensitive,
            )
    except (OSError, RuntimeError, TypeError, ValueError):
        LOGGER.exception("Could not persist local ML task telemetry for job %s.", job["id"])


def _notify_motion_event(event: dict) -> None:
    snapshot = str(event.get("snapshot", "")).strip()
    if snapshot:
        try:
            ts = event.get("timestamp")
            if ts:
                dt = datetime.fromisoformat(ts).astimezone()
                time_str = dt.strftime("%I:%M %p").lstrip("0")
            else:
                time_str = datetime.now().strftime("%I:%M %p").lstrip("0")
        except Exception:
            time_str = datetime.now().strftime("%I:%M %p").lstrip("0")
        title = f"Motion detected in the room, {time_str}"
        thumb = f"/api/camera/snapshot?motion={snapshot}"
        NOTIFIER.notify(
            "camera_motion",
            title,
            severity="info",
            thumbnail=thumb,
            cooldown_seconds=120.0,
            dedupe_key=f"camera-motion:{snapshot}",
        )


def _notify_resource_alerts(metrics: SystemMetrics) -> None:
    disk = shutil.disk_usage(WORKSPACE_DIR)
    disk_ratio = disk.used / max(1, disk.total)
    temp = metrics.temperature_c
    if temp is None:
        try:
            from agent.notifier import read_cpu_temperature
            temp = read_cpu_temperature()
        except Exception:
            temp = None

    if disk_ratio >= 0.85:
        NOTIFIER.notify(
            "disk_full",
            f"Storage is almost full ({disk_ratio * 100:.0f}% used)",
            severity="warning",
            cooldown_seconds=600.0,
            dedupe_key="disk_full",
        )
    if metrics.ram_percent >= 90:
        NOTIFIER.notify(
            "low_memory",
            f"System memory is low ({metrics.ram_percent:.0f}% used)",
            severity="warning",
            cooldown_seconds=300.0,
            dedupe_key="low_memory",
        )
    if temp is not None and temp >= 80:
        NOTIFIER.notify(
            "system_temp",
            f"System heating: CPU temperature is {temp:.0f}°C",
            severity="warning",
            cooldown_seconds=300.0,
            dedupe_key="system_temp",
        )


def _notify_system_anomaly(_event: dict) -> None:
    NOTIFIER.notify(
        "health_alert",
        "Unusual system behavior detected on the Hsx host",
        dedupe_key=f"system-anomaly:{int(time.time() // 300)}",
    )


def _generate_greeting(time_of_day: str) -> str:
    if time_of_day not in {"morning", "afternoon", "evening"}:
        raise ValueError("A valid local time of day is required.")
    router = default_router()
    providers = getattr(router, "providers", [])
    cloud_providers = [provider for provider in providers if provider.name.lower() != "ollama"]
    if not cloud_providers:
        raise RuntimeError("No cloud provider is configured.")
    provider = PROVIDER_BANDIT.rank(cloud_providers, prefer_cloud=True)[0]
    original_providers = list(providers)
    try:
        response = call_llm(
            [{
                "role": "user",
                "content": (
                    "Write one natural, warm welcome line for the owner's Hsx chat home, no more than "
                    f"8 words. It is local {time_of_day}; mention the time only if it sounds natural. "
                    "Address the owner as Sir. Avoid canned wording, made-up personal context, tasks, "
                    "mood, or jokes. No markdown, quotation marks, or explanation."
                ),
            }],
            [],
            target_device="local_host",
            prefer_cloud=True,
            disable_panel=True,
            required_provider=provider.name,
        )
    finally:
        router.providers = original_providers
    text = " ".join(
        " ".join(str(getattr(block, "text", "")).split())
        for block in response.content
        if getattr(block, "type", "") == "text"
    )
    if not text:
        raise RuntimeError("The cloud provider returned an empty greeting.")
    return text[:180]


def _is_user_upload_path(raw_path: str) -> bool:
    parts = Path(raw_path.replace("\\", "/")).parts
    return any(
        part.casefold() == "uploads"
        or re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{4} uploads(?: \(\d+\))?", part.casefold())
        for part in parts
    )


def _camera_status_payload(include_host: bool = True, session_id: str = "") -> dict:
    status = CAMERA.health_check()
    from agent.tools.vision_capture import disabled_reason, enabled

    try:
        session_active = bool(session_id and CAMERA.has_owner(camera_session_owner(session_id)))
    except ValueError:
        session_active = False
    snapshot_available = CAMERA.is_started() or (
        enabled() and bool(status.get("device_available")) and shutil.which("ffmpeg") is not None
    )
    payload = {
        **status,
        "session_active": session_active,
        "snapshot_available": snapshot_available,
        "snapshot_reason": "" if snapshot_available else disabled_reason(),
    }
    if include_host:
        host = system_info()
        payload["host"] = {
            "hostname": host["hostname"],
            "hardware": host["cpu"]["model"],
        }
    return payload


def _session_artifacts(session_id: str) -> list[dict]:
    artifacts = ARTIFACTS.list(session_id)
    if not artifacts:
        ARTIFACTS.migrate_from_workspace(WORKSPACE, session_id, ASK_JOBS, ARTIFACT_SUFFIXES)
        artifacts = ARTIFACTS.list(session_id)
    visible = []
    for artifact in artifacts:
        try:
            path = WORKSPACE.resolve_read(artifact["path"])
        except (OSError, ValueError):
            continue
        if path.is_file() and path.suffix.lower() in ARTIFACT_SUFFIXES:
            visible.append(artifact)
    return visible[:200]


def _register_artifact(session_id: str, raw_path: str) -> None:
    if not raw_path:
        return
    try:
        candidate = Path(raw_path)
        path = candidate.resolve() if candidate.is_absolute() else WORKSPACE.resolve_write(raw_path).resolve()
        WORKSPACE._assert_inside(WORKSPACE.root, path)
        if not path.is_file() or path.suffix.lower() not in ARTIFACT_SUFFIXES:
            return
        relative = path.relative_to(WORKSPACE.root).as_posix()
        ARTIFACTS.record(session_id, relative)
    except (OSError, ValueError):
        LOGGER.warning("Ignoring invalid user-facing artifact path.")


def _fail_job(
    job_id: str,
    job: dict,
    error: Exception,
    *,
    stream: bool = False,
    started_at: float | None = None,
) -> None:
    if job_id in _FAILED_RUN_IDS:
        return
    _FAILED_RUN_IDS.add(job_id)
    error_message = _job_error(error)
    try:
        UNCERTAIN_INTENTS.resolve(job_id, succeeded=False)
    except (OSError, RuntimeError, TypeError, ValueError):
        LOGGER.exception("Could not save uncertain intent failure for job %s.", job_id)
    LOGGER.exception("HSX job %s failed during agent execution: %s", job_id, error_message)
    failed = ASK_JOBS.fail(job_id, error_message)
    if failed and failed["state"] == "cancelled":
        return
    if started_at is not None:
        _record_job_telemetry(
            job,
            time.perf_counter() - started_at,
            succeeded=False,
            history=SESSION_HISTORIES.get(job["session_id"]),
            error_message=error_message,
        )
    if stream:
        _emit_chat_event(job_id, "job", _ask_job_payload(failed))
        _emit_chat_event(job_id, "error", {"message": error_message})
        _emit_chat_event(job_id, "done", {})
    POLICY.audit_event("job_failed", job["session_id"], job_id, "failed")
    _hardware_audio_alert(succeeded=False)


def _resume_ask_job(job_id: str) -> None:
    job = ASK_JOBS.get(job_id)
    if job is None or job["state"] != "running":
        return
    lock = _session_lock(job["session_id"])
    started_at = time.perf_counter()
    device_actions = list(job.get("device_actions") or [])
    resume_history = copy.deepcopy(job["checkpoint"])
    scratch_dir = DATA_DIR / "scratch" / job_id
    scratch_dir.mkdir(parents=True, exist_ok=True)
    try:
        with lock, task_output_scope(WORKSPACE, job["request"], job["task_dir"]):
            response, history = run_agent(
                job["request"],
                resume_history,
                TOOLS,
                SKILL_MAP,
                session_id=job["session_id"],
                sensitive=job.get("sensitive", False),
                resume_tool_result={
                    "tool_call_id": job["tool_call_id"],
                    "answer": job["answers"][-1],
                },
                target_device=job.get("target_device", "client_browser"),
                device_actions=device_actions,
                job_id=job_id,
            )
    except ApprovalRequest as approval:
        updated = ASK_JOBS.pause_approval(
            job_id,
            {
                **approval.approval_request,
                "tool": approval.tool,
                "arguments": approval.arguments,
            },
            approval.checkpoint,
            approval.tool_call_id,
            state=(
                "permission_request"
                if approval.approval_request.get("request_type") == "permission_request"
                else "waiting_for_approval"
            ),
        )
        SESSION_HISTORIES[job["session_id"]] = approval.checkpoint
        ASK_JOBS.save_device_actions(job_id, device_actions)
        _emit_chat_event(job_id, "job", _ask_job_payload(updated))
        permission_request = approval.approval_request.get("request_type") == "permission_request"
        POLICY.audit_event(
            "permission_requested" if permission_request else "approval_requested",
            job["session_id"], job_id, approval.approval_request.get("action_pattern", "permission"),
        )
        NOTIFIER.notify(
            "needs_approval",
            "Hsx needs permission" if permission_request else "Hsx needs approval",
            job_id=job_id,
        )
        return
    except AskUserRequest as question:
        updated = ASK_JOBS.pause(job_id, question)
        SESSION_HISTORIES[job["session_id"]] = question.checkpoint
        ASK_JOBS.save_device_actions(job_id, device_actions)
        if updated["state"] == "waiting_for_user":
            POLICY.audit_event("needs_input", job["session_id"], job_id, "waiting_for_user")
            NOTIFIER.notify(
                "needs_input",
                "Hsx needs your input",
                job_id=job_id,
                dedupe_key=f"{job_id}:question:{updated['question_count']}",
            )
            return
        POLICY.audit_event("question_limit", job["session_id"], job_id, "cancelled")
        return
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as error:
        SESSION_HISTORIES[job["session_id"]] = resume_history
        _fail_job(job_id, job, error, started_at=started_at)
        return
    except Exception as error:
        SESSION_HISTORIES[job["session_id"]] = resume_history
        _fail_job(job_id, job, error, started_at=started_at)
        return
    else:
        SESSION_HISTORIES[job["session_id"]] = history
        ASK_JOBS.complete(job_id, response, device_actions)
        _record_job_telemetry(
            job,
            time.perf_counter() - started_at,
            succeeded=True,
            sensitive=job.get("sensitive", False),
            response=response,
            history=history,
        )
        POLICY.audit_event("job_done", job["session_id"], job_id, "done")
        _hardware_audio_alert(succeeded=True)
    finally:
        if scratch_dir.exists():
            shutil.rmtree(scratch_dir, ignore_errors=True)


def _resume_approval_job(job_id: str, approved: bool) -> None:
    job = ASK_JOBS.get(job_id)
    if job is None or job["state"] != "running":
        return
    lock = _session_lock(job["session_id"])
    started_at = time.perf_counter()
    device_actions = list(job.get("device_actions") or [])
    resume_history = copy.deepcopy(job["checkpoint"])
    approval = job.get("approval") or {}
    tool_name = str(approval.get("tool", ""))
    arguments = approval.get("arguments")
    permission_request = approval.get("request_type") == "permission_request"
    device_tool = tool_name in {"vision_capture", "microphone_start"}
    if not isinstance(arguments, dict) or (tool_name not in SKILL_MAP and not device_tool):
        _fail_job(job_id, job, RuntimeError("The stored approval request is invalid."), started_at=started_at)
        return
    skill = next((item for item in TOOLS if item["name"] == tool_name), None)
    if skill is None and device_tool:
        skill = {"name": tool_name}
    if skill is None:
        _fail_job(job_id, job, RuntimeError("The approved tool is no longer available."), started_at=started_at)
        return

    policy_result = POLICY.decide(
        skill,
        arguments,
        job["session_id"],
        approved=approved,
        approver="user",
    )
    result: dict
    if POLICY.is_stopped():
        result = {"success": False, "error": "Emergency stop is active; the approved action was not run."}
        if device_tool:
            result["_hsx_device_action"] = "camera_start" if tool_name == "vision_capture" else "microphone_start"
    elif not policy_result["allowed"]:
        result = {
            "success": False,
            "error": "The user declined this action."
            if not approved
            else "The action is blocked by the host safety policy.",
        }
        if device_tool:
            result["_hsx_device_action"] = "camera_start" if tool_name == "vision_capture" else "microphone_start"
    else:
        PROVIDER_BANDIT.remember_job_tool(job_id, tool_name)
        tool_input = dict(arguments)
        if tool_name in {"system_execute", "shell"} and permission_request and approved:
            tool_input["_approved_elevation"] = True
        if tool_name in {"audio_play", "camera_record", "music_play"}:
            tool_input["target_device"] = job.get("target_device", "local_host")
        if tool_name == "vision_capture":
            owner = camera_session_owner(job["session_id"])
            try:
                frame = CAMERA.start_session(owner)
                POLICY.audit_event("camera_access", job["session_id"], job_id, "started")
                NOTIFIER.broadcast_event(
                    "camera_state",
                    {
                        "active": True,
                        "session_active": True,
                        "device": CAMERA.device,
                        "session_id": job["session_id"],
                    },
                )
                result = {
                    "success": True,
                    "message": "",
                    "_hsx_device_action": "camera_start",
                    "_frame_data": frame.data if _vision_request(str(job["request"])) else b"",
                }
                if result["_frame_data"]:
                    _emit_chat_event(
                        job_id,
                        "camera_snapshot",
                        {"source": "/api/camera/snapshot"},
                    )
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                result = {
                    "success": False,
                    "error": f"The camera did not start: {error}",
                    "_hsx_device_action": "camera_start",
                }
        elif tool_name == "microphone_start":
            owner = microphone_session_owner(job["session_id"])
            try:
                MICROPHONE.acquire(owner)
                POLICY.audit_event("microphone_access", job["session_id"], job_id, "started")
                result = {
                    "success": True,
                    "message": "The HP microphone is on for this session. Audio is discarded and is not recorded.",
                    "_hsx_device_action": "microphone_start",
                }
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                result = {
                    "success": False,
                    "error": f"The microphone did not start: {error}",
                    "_hsx_device_action": "microphone_start",
                }
        else:
            try:
                result = SKILL_MAP[tool_name](**tool_input)
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                result = {"success": False, "error": str(error)}
        if isinstance(result, dict) and tool_name in {"audio_play", "camera_record", "music_play"}:
            device_actions.append(
                result.get("browser_action")
                or {
                    "type": tool_name,
                    "target_device": job.get("target_device", "local_host"),
                    "success": result.get("success", False),
                    "message": result.get("message", ""),
                    "path": result.get("path"),
                    "pid": result.get("pid"),
                }
            )

    try:
        with lock, task_output_scope(WORKSPACE, job["request"], job["task_dir"]):
            response, history = run_agent(
                job["request"],
                resume_history,
                TOOLS,
                SKILL_MAP,
                session_id=job["session_id"],
                approvals=None,
                sensitive=job.get("sensitive", False),
                resume_tool_result={"tool_call_id": job["tool_call_id"], "result": result},
                target_device=job.get("target_device", "local_host"),
                device_actions=device_actions,
                job_id=job_id,
            )
    except ApprovalRequest as next_approval:
        updated = ASK_JOBS.pause_approval(
            job_id,
            {
                **next_approval.approval_request,
                "tool": next_approval.tool,
                "arguments": next_approval.arguments,
            },
            next_approval.checkpoint,
            next_approval.tool_call_id,
            state=(
                "permission_request"
                if next_approval.approval_request.get("request_type") == "permission_request"
                else "waiting_for_approval"
            ),
        )
        SESSION_HISTORIES[job["session_id"]] = next_approval.checkpoint
        ASK_JOBS.save_device_actions(job_id, device_actions)
        _emit_chat_event(job_id, "job", _ask_job_payload(updated))
        request_type = next_approval.approval_request.get("request_type")
        POLICY.audit_event(
            "permission_requested" if request_type == "permission_request" else "approval_requested",
            job["session_id"],
            job_id,
            next_approval.approval_request.get("action_pattern", "permission"),
        )
        NOTIFIER.notify(
            "needs_approval",
            "Hsx needs permission" if request_type == "permission_request" else "Hsx needs approval",
            job_id=job_id,
        )
        return
    except AskUserRequest as question:
        updated = ASK_JOBS.pause(job_id, question)
        SESSION_HISTORIES[job["session_id"]] = question.checkpoint
        ASK_JOBS.save_device_actions(job_id, device_actions)
        _emit_chat_event(job_id, "job", _ask_job_payload(updated))
        if updated["state"] == "waiting_for_user":
            POLICY.audit_event("needs_input", job["session_id"], job_id, "waiting_for_user")
            NOTIFIER.notify(
                "needs_input",
                "Hsx needs your input",
                job_id=job_id,
                dedupe_key=f"{job_id}:question:{updated['question_count']}",
            )
        else:
            POLICY.audit_event("question_limit", job["session_id"], job_id, "cancelled")
        return
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as error:
        SESSION_HISTORIES[job["session_id"]] = resume_history
        _fail_job(job_id, job, error, started_at=started_at)
        return
    except Exception as error:
        SESSION_HISTORIES[job["session_id"]] = resume_history
        _fail_job(job_id, job, error, started_at=started_at)
        return

    SESSION_HISTORIES[job["session_id"]] = history
    ASK_JOBS.complete(job_id, response, device_actions)
    _record_job_telemetry(
        job,
        time.perf_counter() - started_at,
        succeeded=True,
        sensitive=job.get("sensitive", False),
        response=response,
        history=history,
    )
    _emit_chat_event(job_id, "job", _ask_job_payload(ASK_JOBS.get(job_id)))
    _emit_chat_event(job_id, "done", {})
    POLICY.audit_event("job_done", job["session_id"], job_id, "done")


def _session_lock(session_id: str):
    with SESSION_LOCKS_GUARD:
        if session_id not in SESSION_LOCKS:
            SESSION_LOCKS[session_id] = threading.Lock()
        return SESSION_LOCKS[session_id]


def _task_result_path(job: dict) -> str:
    directory = Path(job["task_dir"]) if job.get("task_dir") else Path(".")
    try:
        files = workspace_search(
            WORKSPACE,
            directory=(WORK_DIR.relative_to(WORKSPACE_DIR) / directory),
            limit=1,
        )
    except (OSError, ValueError):
        return ""
    return files[0]["path"] if files else ""


def _run_chat_job(
    job_id: str,
    approvals: dict | None = None,
    sensitive: bool = False,
    target_device: str = "client_browser",
    on_text_delta: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
    on_provider_notice: Callable[[str], None] | None = None,
    on_activity_event: Callable[[str, dict], None] | None = None,
) -> None:
    job = ASK_JOBS.get(job_id)
    if job is None or job["state"] != "running":
        return
    lock = _session_lock(job["session_id"])
    started_at = time.perf_counter()
    POLICY.audit_event("job_started", job["session_id"], job_id, "running")
    device_actions = list(job.get("device_actions") or [])
    activity_snapshots = {}
    activity_calls = {}

    def emit_activity(event: str, payload: dict) -> None:
        call_id = str(payload.get("tool_call_id", ""))
        if event == "tool_start":
            arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
            if len(json.dumps(arguments, ensure_ascii=False)) <= 65536:
                activity_calls[call_id] = {
                    "tool": payload.get("tool", ""),
                    "arguments": copy.deepcopy(arguments),
                    "start": copy.deepcopy(payload),
                    "session_id": job["session_id"],
                    "target_device": job.get("target_device", "client_browser"),
                    "task_dir": job.get("task_dir", ""),
                }
            raw_path = payload.get("file_path") or next(
                (arguments.get(key) for key in ("path", "filename", "file") if isinstance(arguments.get(key), str)),
                "",
            )
            path = _activity_resolve_path(raw_path) if raw_path else None
            activity_snapshots[call_id] = _activity_snapshot(path)
            if path is not None:
                payload["file_path"] = _activity_display_path(str(path))
        elif event == "tool_end":
            arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
            raw_path = payload.get("file_path") or next(
                (arguments.get(key) for key in ("path", "filename", "file") if isinstance(arguments.get(key), str)),
                "",
            )
            after_path = _activity_resolve_path(raw_path) if raw_path else None
            after = _activity_snapshot(after_path)
            before = activity_snapshots.pop(call_id, None)
            if payload.get("tool") in _ACTIVITY_FILE_TOOLS and after_path is not None:
                relative_path = _activity_display_path(str(after_path)) or after_path.name
                diff = _activity_diff(before, after, relative_path)
                if diff is not None:
                    payload["diff"] = diff
                    payload["file_path"] = relative_path
                    if before is None and diff["added"]:
                        payload.update({"icon": "file-plus", "present": "Creating", "past": "Created"})
            payload["images"] = _register_activity_images(
                payload.get("images", []) if isinstance(payload.get("images"), list) else []
            )
            if payload.get("status") == "failed" and call_id in activity_calls:
                retry_call = {
                    **activity_calls[call_id],
                    "job_id": job_id,
                    "created_at": time.time(),
                }
                with _ACTIVITY_RETRY_LOCK:
                    _ACTIVITY_RETRY_CALLS[call_id] = retry_call
                    while len(_ACTIVITY_RETRY_CALLS) > 100:
                        _ACTIVITY_RETRY_CALLS.pop(next(iter(_ACTIVITY_RETRY_CALLS)))
        elif event == "activity_narration":
            payload = {"text": redact_activity_text(str(payload.get("text", "")), 1000)}
        safe_payload = redact_activity_value(payload, limit=2000)
        _persist_activity_event(job.get("task_dir", ""), event, safe_payload, job_id=job_id)
        if event == "tool_end" and safe_payload.get("artifact") is True:
            file_path = str(safe_payload.get("file_path", "")).strip()
            if file_path:
                _register_artifact(job["session_id"], file_path)
        _emit_chat_event(job_id, event, safe_payload)

    scratch_dir = DATA_DIR / "scratch" / job_id
    scratch_dir.mkdir(parents=True, exist_ok=True)
    try:
        with lock, task_output_scope(WORKSPACE, job["request"], job["task_dir"]):
            history = copy.deepcopy(SESSION_HISTORIES.get(job["session_id"], []))
            response, updated_history = run_agent(
                job["request"],
                history,
                TOOLS,
                SKILL_MAP,
                session_id=job["session_id"],
                approvals=None,
                sensitive=sensitive,
                target_device=job.get("target_device", target_device),
                device_actions=device_actions,
                on_text_delta=on_text_delta,
                on_provider_notice=on_provider_notice,
                on_activity_event=emit_activity if on_activity_event is not None else None,
                job_id=job_id,
                cancel_event=cancel_event,
            )
    except ApprovalRequest as approval:
        updated = ASK_JOBS.pause_approval(
            job_id,
            {
                **approval.approval_request,
                "tool": approval.tool,
                "arguments": approval.arguments,
            },
            approval.checkpoint,
            approval.tool_call_id,
            state=(
                "permission_request"
                if approval.approval_request.get("request_type") == "permission_request"
                else "waiting_for_approval"
            ),
        )
        SESSION_HISTORIES[job["session_id"]] = approval.checkpoint
        ASK_JOBS.save_device_actions(job_id, device_actions)
        _emit_chat_event(job_id, "job", _ask_job_payload(updated))
        _emit_chat_event(job_id, "done", {})
        if updated["state"] not in {"waiting_for_approval", "permission_request"}:
            return
        POLICY.audit_event(
            "permission_requested"
            if approval.approval_request.get("request_type") == "permission_request"
            else "approval_requested",
            job["session_id"],
            job_id,
            approval.approval_request.get("action_pattern", "permission"),
        )
        NOTIFIER.notify(
            "needs_approval",
            "Hsx needs permission"
            if approval.approval_request.get("request_type") == "permission_request"
            else "Hsx needs approval",
            job_id=job_id,
        )
        return
    except AskUserRequest as question:
        updated = ASK_JOBS.pause(job_id, question)
        SESSION_HISTORIES[job["session_id"]] = question.checkpoint
        ASK_JOBS.save_device_actions(job_id, device_actions)
        _emit_chat_event(job_id, "job", _ask_job_payload(updated))
        _emit_chat_event(job_id, "done", {})
        if updated["state"] == "cancelled":
            return
        if updated["state"] == "waiting_for_user":
            POLICY.audit_event("needs_input", job["session_id"], job_id, "waiting_for_user")
            NOTIFIER.notify(
                "needs_input",
                "Hsx needs your input",
                job_id=job_id,
                dedupe_key=f"{job_id}:question:{updated['question_count']}",
            )
        else:
            POLICY.audit_event("question_limit", job["session_id"], job_id, "cancelled")
        return
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as error:
        _fail_job(job_id, job, error, stream=True, started_at=started_at)
        return
    except Exception as error:
        _fail_job(job_id, job, error, stream=True, started_at=started_at)
        return
    else:
        latest = ASK_JOBS.get(job_id)
        if latest is None or latest["state"] != "running":
            return
        SESSION_HISTORIES[job["session_id"]] = updated_history
        completed = ASK_JOBS.complete(job_id, response, device_actions)
        if completed is None or completed["state"] != "done":
            return
        _record_job_telemetry(
            job,
            time.perf_counter() - started_at,
            succeeded=True,
            sensitive=sensitive,
            response=response,
            history=updated_history,
        )
        _emit_chat_event(job_id, "job", _ask_job_payload(ASK_JOBS.get(job_id)))
        _emit_chat_event(job_id, "done", {})
        POLICY.audit_event("job_done", job["session_id"], job_id, "done")
        _hardware_audio_alert(succeeded=True)
    finally:
        if scratch_dir.exists():
            shutil.rmtree(scratch_dir, ignore_errors=True)


class HsxHandler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *args) -> None:
        del _format
        request_line = str(args[0]) if args else ""
        method, separator, target = request_line.partition(" ")
        if not separator:
            method, target = "HTTP", ""
        else:
            target = target.split(" ", 1)[0]
        path = unquote(urlsplit(target).path)
        if AUTH_TOKEN:
            path = path.replace(AUTH_TOKEN, "[redacted]")
        status = str(args[1]) if len(args) > 1 else "-"
        size = str(args[2]) if len(args) > 2 else "-"
        LOGGER.info('HTTP %s "%s %s" %s %s', self.client_address[0], method, path, status, size)

    def _security_headers(self) -> None:
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; connect-src 'self' https://cdn.jsdelivr.net https://storage.googleapis.com; "
            "font-src 'self' data:; form-action 'self'; "
            "img-src 'self' data: blob:; media-src 'self' blob:; "
            "object-src 'none'; script-src 'self' https://cdn.jsdelivr.net 'wasm-unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(self), microphone=(self)")

    def _request_path(self) -> str:
        return urlsplit(self.path).path

    def _api_access_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is not None:
            if origin != "null" and origin in ALLOWED_ORIGINS:
                self._authorized_remote_control = True
                return True
            self._json(
                {"error": "Origin not allowed. Add this exact scheme, host, and port to HSX_ALLOWED_ORIGINS."},
                403,
            )
            return False

        if self.headers.get("Sec-Fetch-Site") == "same-origin":
            request_host = self.headers.get("Host", "").casefold()
            allowed_hosts = {
                urlsplit(allowed_origin).netloc.casefold()
                for allowed_origin in ALLOWED_ORIGINS
                if urlsplit(allowed_origin).scheme in {"http", "https"}
            }
            if request_host and request_host in allowed_hosts:
                self._authorized_remote_control = True
                return True

        scheme, separator, token = self.headers.get("Authorization", "").partition(" ")
        if separator and scheme.lower() == "bearer" and token.isascii() and hmac.compare_digest(token, AUTH_TOKEN):
            self._authorized_remote_control = True
            return True
        self._json(
            {"error": "This request has no Origin header. Supply an Authorization: Bearer token from data/auth.token."},
            401,
        )
        return False

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._cors_headers()
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def _stream_notifications(self, query: dict[str, list[str]]) -> None:
        try:
            last_id = int(self.headers.get("Last-Event-ID", query.get("after", ["0"])[0]))
        except ValueError:
            self._json({"error": "Last-Event-ID must be an integer."}, 400)
            return
        self.send_response(200)
        self._security_headers()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self._cors_headers()
        self.end_headers()
        last_broadcast_id = 0
        try:
            while True:
                broadcasts = NOTIFIER.get_broadcast_events_after(last_broadcast_id)
                for b in broadcasts:
                    payload = json.dumps(b["data"], separators=(",", ":"))
                    self.wfile.write(f"event: {b['event']}\ndata: {payload}\n\n".encode())
                    last_broadcast_id = b["id"]
                notifications = NOTIFIER.wait_for_notifications(last_id, timeout=15)
                broadcasts = NOTIFIER.get_broadcast_events_after(last_broadcast_id)
                for b in broadcasts:
                    payload = json.dumps(b["data"], separators=(",", ":"))
                    self.wfile.write(f"event: {b['event']}\ndata: {payload}\n\n".encode())
                    last_broadcast_id = b["id"]
                if not notifications and not broadcasts:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                for notification in notifications:
                    data = json.dumps(notification, separators=(",", ":"))
                    self.wfile.write(f"id: {notification['id']}\nevent: notification\ndata: {data}\n\n".encode())
                    last_id = notification["id"]
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    def _stream_camera(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        try:
            owner = camera_session_owner(str(query.get("session_id", [""])[0]))
        except ValueError:
            self._json({"error": "Turn on the camera in this browser session first."}, 403)
            return
        if not CAMERA.has_owner(owner):
            self._json({"error": "Turn on the camera in this browser session first."}, 403)
            return
        frame = CAMERA.latest_frame()
        if frame is None:
            frame = CAMERA.wait_for_frame(-1, timeout=5.0)
        if frame is None:
            self._json(
                {"error": "Camera frame is unavailable.", "camera": CAMERA.health_check()},
                503,
            )
            return

        self.send_response(200)
        self._security_headers()
        self._cors_headers()
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Hsx-Server-Time", f"{time.time():.6f}")
        self.end_headers()
        version = -1
        try:
            self.connection.settimeout(5.0)
            while True:
                self.wfile.write(
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(frame.data)}\r\n".encode("ascii")
                    + f"X-Hsx-Captured-At: {frame.captured_at_wall or time.time():.6f}\r\n\r\n".encode("ascii")
                    + frame.data
                    + b"\r\n"
                )
                self.wfile.flush()
                version = frame.version
                frame = CAMERA.wait_for_frame(version, timeout=MAX_FRAME_AGE_SECONDS)
                if frame is None:
                    return
        except (OSError, TimeoutError):
            return

    def do_GET(self) -> None:
        path = self._request_path()
        if path.startswith("/api/music/stream/"):
            stream_id = path.removeprefix("/api/music/stream/")
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            auth_cookie = cookie.get("hsx_audio")
            audio_path = MUSIC_CATALOG.resolve_stream(
                stream_id,
                auth_cookie.value if auth_cookie else "",
            )
            if audio_path is None:
                self._json({"error": "Music stream is unavailable or expired."}, 404)
                return
            self._serve_file(audio_path, inline=True, no_store=True)
            return
        if path.startswith("/api/") and not self._api_access_allowed():
            return
        query = parse_qs(urlsplit(self.path).query)
        if path == "/api/auth/check":
            self._json({"authenticated": True})
            return
        if path == "/api/mesh/info":
            self._json(
                {"enabled": False}
                if MESH_GATEWAY is None
                else {"enabled": True, **MESH_GATEWAY.node_info()}
            )
            return
        elif path == "/api/mesh/telemetry":
            telemetry = LLM_TELEMETRY.snapshot()
            telemetry["provider_stats"] = PROVIDER_BANDIT.snapshot()["providers"]
            self._json(telemetry)
            return
        elif path == "/api/mesh/camera/status":
            self._json(_camera_status_payload())
            return
        elif path == "/api/mesh/camera/stream":
            self._stream_camera()
            return
        elif path.startswith("/api/mesh/jobs/"):
            path = "/api/ask-jobs/" + path.removeprefix("/api/mesh/jobs/")
        if path == "/healthz":
            warnings = []
            if not Path("/run/systemd/timesync/synchronized").exists():
                warnings.append("System clock sync status is unavailable or not synchronized.")
            self._json({"status": "ok", "warnings": warnings})
        elif path == "/healthz/providers":
            try:
                providers = default_router().providers
            except (RuntimeError, ValueError, OSError) as error:
                self._json({"providers": [], "error": str(error)}, 503)
                return
            results = []
            request = LLMRequest(messages=[{"role": "user", "content": "Reply with OK."}], system="Reply with OK.")
            for provider in providers:
                result = {
                    "provider": provider.name,
                    "model": getattr(provider, "model", "unknown"),
                }
                try:
                    events = list(provider.generate(request))
                    if not any(event.type in {"text_delta", "tool_call"} for event in events):
                        raise ProviderError("Provider returned no response content.", provider=provider.name)
                except Exception as error:
                    result.update({"status": "failed", "reason": str(error)[:240]})
                else:
                    result["status"] = "ok"
                results.append(result)
            self._json({"providers": results})
        elif path == "/health":
            self._json({"status": "ok"})
        elif path == "/api/skills":
            self._json({"skills": [tool["name"] for tool in TOOLS]})
        elif path == "/api/policy/status":
            self._json({"stopped": POLICY.is_stopped()})
        elif path == "/api/permissions":
            self._json({
                "mode": "always_ask",
                "message": "Hsx asks for one-time approval before every tool action and explains its likely consequence.",
            })
        elif path == "/api/runs/status":
            run_id = str(parse_qs(urlsplit(self.path).query).get("run_id", [""])[0])
            event = RUN_CANCEL_EVENTS.get(run_id)
            job = ASK_JOBS.get(run_id) if run_id else None
            self._json({"state": job["state"] if job else "unknown", "cancelled": bool(event and event.is_set())})
        elif path == "/api/telemetry":
            telemetry = LLM_TELEMETRY.snapshot()
            telemetry["provider_stats"] = PROVIDER_BANDIT.snapshot()["providers"]
            self._json(telemetry)
        elif path == "/api/camera/status":
            self._json(
                _camera_status_payload(
                    include_host=False,
                    session_id=str(query.get("session_id", [""])[0]),
                )
            )
        elif path == "/api/camera/stream":
            self._stream_camera()
            return
        elif path == "/api/mode":
            self._json({"mode": MODE})
        elif path == "/api/jobs":
            self._json({"jobs": JOBS.list()})
        elif path == "/api/ask-jobs":
            self._json({"jobs": [_ask_job_payload(job) for job in ASK_JOBS.list()]})
        elif path == "/api/ask-jobs/pending":
            session_id = str(query.get("session_id", [""])[0])[:100]
            if not session_id:
                self._json({"error": "A session_id is required."}, 400)
                return
            self._json({"job": _ask_job_payload(ASK_JOBS.get_pending(session_id))})
        elif path == "/api/activity":
            session_id = str(query.get("session_id", [""])[0])[:100]
            if not session_id:
                self._json({"error": "A session_id is required."}, 400)
                return
            self._json({"tasks": ASK_JOBS.activity(session_id)})
        elif path.startswith("/api/ask-jobs/"):
            job = ASK_JOBS.get(path.removeprefix("/api/ask-jobs/"))
            self._json({"job": _ask_job_payload(job)} if job else {"error": "Job not found."}, 200 if job else 404)
        elif path.startswith("/api/jobs/"):
            job = JOBS.get(path.removeprefix("/api/jobs/"))
            self._json({"job": job} if job else {"error": "Job not found."}, 200 if job else 404)
        elif path == "/api/improvements":
            self._json({"proposals": IMPROVEMENTS.list()})
        elif path == "/api/devices":
            self._json({"devices": DEVICES.list()})
        elif path == "/api/body":
            self._json(system_body_state())
        elif path == "/api/memory":
            self._json({"facts": MEMORY.list_facts()})
        elif path == "/api/notifications":
            notifications, unread = NOTIFIER.list_notifications()
            self._json({
                "notifications": notifications,
                "unread": unread,
                "temperature": NOTIFIER.get_temperature(),
            })
        elif path == "/api/notifications/settings":
            self._json({
                "settings": NOTIFIER.get_settings(),
                "temperature": NOTIFIER.get_temperature(),
                "motion_active": MOTION_MONITOR.health_check().get("running", False),
            })
        elif path == "/api/camera/snapshot":
            motion_name = query.get("motion", [""])[0].strip()
            if motion_name:
                if not re.fullmatch(r"[a-zA-Z0-9_\-\.]+", motion_name):
                    self._json({"error": "Invalid snapshot name."}, 400)
                    return
                snap_path = (DATA_DIR / "snapshots" / "motion" / motion_name).resolve()
                motion_dir = (DATA_DIR / "snapshots" / "motion").resolve()
                if not snap_path.is_file() or not str(snap_path).startswith(str(motion_dir)):
                    self._json({"error": "Snapshot not found."}, 404)
                    return
                data = snap_path.read_bytes()
            else:
                frame = CAMERA.latest_frame()
                if not frame or not frame.data:
                    self._json({"error": "No camera frame available."}, 404)
                    return
                data = frame.data
            self.send_response(200)
            self._security_headers()
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(data)
            return
        elif path == "/api/conversations":
            self._json({"conversations": CONVERSATIONS.list()})
        elif path.startswith("/api/conversations/"):
            session_id = unquote(path.removeprefix("/api/conversations/"))[:100]
            conversation = CONVERSATIONS.get(session_id)
            self._json(conversation or {"error": "Conversation not found."}, 200 if conversation else 404)
        elif path == "/api/feedback/settings":
            try:
                self._json(FEEDBACK_STORE.settings())
            except (OSError, sqlite3.Error) as error:
                LOGGER.warning("Feedback settings request failed: %s", error)
                self._json({"error": "Feedback settings are temporarily unavailable."}, 503)
        elif path == "/api/notifications/stream":
            self._stream_notifications(query)
            return
        elif path == "/api/notifications/webpush/config":
            self._json({"public_key": NOTIFIER.public_key, "enabled": bool(NOTIFIER.public_key)})
        elif path == "/api/notifications/webpush/subscriptions":
            self._json({"subscriptions": NOTIFIER.list_subscriptions()})
        elif path == "/api/files/preview":
            relative_path = query.get("path", [""])[0]
            try:
                content = WORKSPACE.read_text(relative_path, max_bytes=2_000_000, max_chars=50_000)
            except (OSError, ValueError) as error:
                self._json({"error": str(error)}, 400)
                return
            self._json({"content": content})
        elif path == "/api/activity/image":
            image_id = query.get("id", [""])[0]
            with _ACTIVITY_IMAGE_LOCK:
                image_path = _ACTIVITY_IMAGE_PATHS.get(image_id)
            safe_path = _activity_resolve_path(str(image_path)) if image_path else None
            if safe_path is None:
                self._json({"error": "Activity image is unavailable."}, 404)
                return
            if query.get("thumbnail", ["1"])[0] == "0":
                self._serve_file(safe_path, inline=True)
                return
            if safe_path.stat().st_size > 20 * 1024 * 1024:
                self._json({"error": "Activity image exceeds the preview size limit."}, 413)
                return
            cache_key = (image_id, safe_path.stat().st_mtime_ns, safe_path.stat().st_size)
            with _ACTIVITY_IMAGE_LOCK:
                thumbnail = _ACTIVITY_THUMBNAILS.get(cache_key)
            if thumbnail is None:
                try:
                    from PIL import Image

                    with Image.open(safe_path) as image:
                        image.thumbnail((256, 256))
                        image.convert("RGB").save(
                            (buffer := io.BytesIO()),
                            format="JPEG",
                            quality=76,
                            optimize=True,
                        )
                    thumbnail = buffer.getvalue()
                except (ImportError, OSError, ValueError) as error:
                    self._json({"error": f"Image preview is unavailable: {error}"}, 415)
                    return
                with _ACTIVITY_IMAGE_LOCK:
                    if len(_ACTIVITY_THUMBNAILS) >= 256:
                        _ACTIVITY_THUMBNAILS.pop(next(iter(_ACTIVITY_THUMBNAILS)))
                    _ACTIVITY_THUMBNAILS[cache_key] = thumbnail
            self.send_response(200)
            self._security_headers()
            self._cors_headers()
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(thumbnail)))
            self.end_headers()
            self.wfile.write(thumbnail)
        elif path == "/api/files":
            try:
                files = workspace_search(
                    WORKSPACE,
                    query=query.get("q", [""])[0],
                    directory=query.get("path", ["."])[0],
                    file_type=query.get("type", [""])[0],
                    recent_days=int(query["recent_days"][0]) if "recent_days" in query else None,
                    limit=int(query.get("limit", ["100"])[0]),
                )
            except (OSError, ValueError) as error:
                self._json({"error": str(error)}, 400)
                return
            self._json({"files": files})
        elif path in {"/api/files/content", "/api/files/download"}:
            relative_path = query.get("path", [""])[0]
            try:
                target = WORKSPACE.resolve_read(relative_path)
                if not target.is_file():
                    raise FileNotFoundError("Workspace file not found.")
            except (OSError, ValueError) as error:
                self._json({"error": str(error)}, 404)
                return
            self._serve_file(target, inline=path.endswith("/content"))
        elif path == "/api/outputs":
            session_id = str(query.get("session_id", [""])[0])[:100]
            files = _session_artifacts(session_id)
            self._json({"files": files})
        elif self.path == "/api/video/jobs":
            self._json({"jobs": list_video_jobs()})
        elif path.startswith("/api/video/jobs/"):
            job_id = path.removeprefix("/api/video/jobs/")
            job = get_video_job(job_id)
            if job is None:
                self._json({"error": "Job not found."}, 404)
                return
            self._json({"job": job})
        elif self.path == "/api/state":
            self._json({
                "skills": [tool["name"] for tool in TOOLS],
                "memory": MEMORY.list_facts(),
                "outputs": self._recent_outputs(),
                "uploads": [{"name": p.name, "type": p.suffix[1:]} for p in UPLOADS_DIR.iterdir() if p.is_file()],
                "video_jobs": list_video_jobs(),
            })
        elif path.startswith("/api/outputs/"):
            relative_path = unquote(path.removeprefix("/api/outputs/"))
            try:
                output = resolve_output(WORKSPACE, relative_path)
            except (OSError, ValueError) as error:
                self._json({"error": str(error)}, 404)
                return
            self._serve_file(output, inline=False)
        else:
            self._serve_frontend()

    def do_POST(self) -> None:
        path = self._request_path()
        if not self._api_access_allowed():
            return
        try:
            request_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json({"error": "Request size is invalid."}, 400)
            return
        if request_length < 0 or request_length > 8 * 1024 * 1024:
            if request_length <= 16 * 1024 * 1024:
                remaining = request_length
                while remaining:
                    chunk = self.rfile.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            self._json({"error": "Request is too large. Reduce the upload and try again."}, 413)
            return
        if path == "/api/music/stream-ticket":
            payload = self._body()
            stream_id = str(payload.get("stream_id", ""))
            secret = MUSIC_CATALOG.authorize_stream(stream_id)
            if secret is None:
                self._json({"error": "Music stream is unavailable or expired."}, 404)
                return
            origin = self.headers.get("Origin", "")
            forwarded_proto = self.headers.get("X-Forwarded-Proto", "").casefold()
            secure = "; Secure" if origin.startswith("https://") or forwarded_proto == "https" else ""
            self.send_response(204)
            self._security_headers()
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Set-Cookie",
                f"hsx_audio={secret}; HttpOnly; SameSite=Strict; Path=/api/music/stream/; "
                f"Max-Age={MUSIC_CATALOG.stream_ttl_seconds}{secure}",
            )
            self.end_headers()
            return
        if path == "/api/mesh/tasks":
            path = "/api/chat/stream"
        if path == "/api/activity/retry":
            payload = self._body()
            try:
                result, status = _retry_activity_tool(
                    str(payload.get("job_id", "")),
                    str(payload.get("tool_call_id", "")),
                )
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                self._json({"error": f"Could not retry the failed step: {error}"}, 500)
                return
            self._json(result, status)
        elif path == "/api/permissions":
            self._json({"error": "Persistent permission rules are disabled; Hsx requests one-time approval for each action."}, 410)
        elif path == "/api/camera/start":
            try:
                payload = self._body()
                owner = camera_session_owner(payload.get("session_id", "") if isinstance(payload, dict) else "")
            except (json.JSONDecodeError, ValueError):
                self._json({"error": "A valid session_id is required to consent to camera use."}, 400)
                return
            session_id = str(payload["session_id"])
            try:
                CAMERA.start_session(owner)
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                self._json({
                    "started": False,
                    "error": str(error),
                    **_camera_status_payload(session_id=str(payload["session_id"])),
                }, 503)
                return
            POLICY.audit_event("camera_access", session_id, "", "started_from_ui")
            NOTIFIER.broadcast_event(
                "camera_state",
                {
                    "active": True,
                    "session_active": True,
                    "device": CAMERA.device,
                    "session_id": session_id,
                },
            )
            self._json({"started": True, **_camera_status_payload(session_id=session_id)})
        elif path == "/api/camera/stop":
            try:
                payload = self._body()
                owner = camera_session_owner(payload.get("session_id", "") if isinstance(payload, dict) else "")
            except (json.JSONDecodeError, ValueError):
                self._json({"error": "A valid session_id is required to stop this camera session."}, 400)
                return
            CAMERA.release(owner)
            active = CAMERA.is_started()
            session_id = str(payload["session_id"])
            POLICY.audit_event("camera_off", session_id, "", "stopped_from_ui")
            NOTIFIER.broadcast_event(
                "camera_state",
                {"active": active, "session_active": False, "session_id": session_id},
            )
            self._json({
                "stopped": True,
                "active": active,
                "session_active": CAMERA.has_owner(owner),
                **_camera_status_payload(session_id=session_id),
            })
        elif path == "/api/conversations":
            payload = self._body()
            try:
                CONVERSATIONS.save(
                    str(payload.get("session_id", "")),
                    str(payload.get("title", "New chat")),
                    payload.get("thread", {}),
                )
            except (OSError, sqlite3.Error, TypeError, ValueError) as error:
                self._json({"error": str(error)}, 400)
                return
            self._json({"success": True}, 201)
        elif path.startswith("/api/conversations/"):
            session_id = unquote(path.removeprefix("/api/conversations/"))[:100]
            payload = self._body()
            if not CONVERSATIONS.rename(session_id, str(payload.get("title", ""))):
                self._json({"error": "Conversation not found."}, 404)
                return
            self._json({"success": True})
        elif path == "/api/feedback/settings":
            payload = self._body()
            try:
                result = FEEDBACK_STORE.update_settings(
                    save_feedback=bool(payload.get("save_feedback")),
                    include_full_conversation=bool(payload.get("include_full_conversation")),
                    include_file_contents=bool(payload.get("include_file_contents")),
                )
            except (OSError, sqlite3.Error, ValueError) as error:
                self._json({"error": str(error)}, 400)
                return
            self._json(result)
        elif path == "/api/feedback/delete":
            payload = self._body()
            if payload.get("confirm") is not True:
                self._json({"error": "Confirm deletion to remove saved feedback."}, 400)
                return
            try:
                deleted = FEEDBACK_STORE.delete_all()
                settings = FEEDBACK_STORE.settings()
            except (OSError, sqlite3.Error) as error:
                LOGGER.warning("Feedback deletion failed: %s", error)
                self._json({"error": "Feedback could not be deleted right now."}, 503)
                return
            self._json({"deleted": deleted, **settings})
        elif path == "/api/greeting":
            payload = self._body()
            time_of_day = str(payload.get("time_of_day", "")).strip().lower()
            try:
                greeting = _generate_greeting(time_of_day)
            except ValueError as error:
                self._json({"error": str(error)}, 400)
                return
            except (RuntimeError, OSError, TypeError):
                LOGGER.warning("Could not generate the greeting with a cloud provider.")
                self._json({"error": "A cloud-generated greeting is temporarily unavailable."}, 503)
                return
            self._json({"greeting": greeting})
        elif path == "/api/feedback":
            payload = self._body()
            job_id = str(payload.get("job_id", "")).strip()
            feedback = str(payload.get("feedback", "")).strip().lower()
            conversation_id = str(payload.get("conversation_id", "")).strip()
            message_id = str(payload.get("message_id", "")).strip()
            if feedback not in {"good", "bad", "up", "down"}:
                self._json({"error": "Feedback rating must be up or down."}, 400)
                return
            job = ASK_JOBS.get(job_id) if job_id else None
            if job_id and job is None:
                self._json({"error": "Chat job not found."}, 404)
                return
            if job:
                conversation_id = conversation_id or job["session_id"]
                message_id = message_id or job_id
                provenance = PROVIDER_BANDIT.job_provenance(job_id)
                tool_names = provenance["tool_names"]
                prompt_messages = [{
                    "role": "user",
                    "content": job["request"],
                }]
                full_conversation = SESSION_HISTORIES.get(job["session_id"], [])
                try:
                    saved = FEEDBACK_STORE.record(
                        conversation_id=conversation_id,
                        message_id=message_id,
                        rating="up" if feedback in {"good", "up"} else "down",
                        reason=str(payload.get("reason", "")),
                        note=str(payload.get("note", "")),
                        provider=provenance["provider"],
                        model=provenance["model"],
                        provider_calls=provenance["provider_calls"],
                        tool_names=tool_names,
                        app_version=os.getenv("HSX_APP_VERSION", "0.0.0+local"),
                        client=str(payload.get("client", "web")),
                        prompt_messages=prompt_messages,
                        response=str(job.get("result", "")),
                        full_conversation=full_conversation,
                    )
                except (OSError, sqlite3.Error, TypeError, ValueError) as error:
                    self._json({"error": str(error)}, 400)
                    return
                if saved:
                    ROUTER_LOGGER.log_feedback(conversation_id, message_id, "good" if feedback in {"good", "up"} else "bad")
                    record_provider_feedback(job_id, "good" if feedback in {"good", "up"} else "bad")
            else:
                saved = False
            self._json({"success": True, "saved": saved, "job_id": job_id, "feedback": feedback}, 201)
        elif path == "/api/chat":
            payload = self._body()
            message = str(payload.get("message", "")).strip()
            if not message:
                self._json({"error": "Message is required."}, 400)
                return
            feedback = message.lower()
            if feedback in {"/good", "/bad"}:
                session_id = str(payload.get("session_id", "default"))[:100]
                job_id = SESSION_LAST_JOBS.get(session_id)
                if job_id is None:
                    self._json({"error": "There is no recent task to rate."}, 404)
                    return
                ROUTER_LOGGER.log_feedback(session_id, job_id, feedback[1:])
                record_provider_feedback(job_id, feedback[1:])
                self._json({"response": "Thanks for the feedback.", "feedback": feedback[1:]})
                return
            try:
                target_device = resolve_target_device(
                    str(payload.get("target_device", "client_browser")),
                    self.client_address[0],
                    allow_remote_local_host=getattr(self, "_authorized_remote_control", False),
                )
            except (ValueError, PermissionError) as error:
                self._json({"error": str(error)}, 400 if isinstance(error, ValueError) else 403)
                return
            session_id = str(payload.get("session_id", "default"))[:100]
            task_directory = WORKSPACE.create_task_dir(message).relative_to(WORK_DIR).as_posix()
            job = ASK_JOBS.create_running(
                message,
                session_id,
                task_directory,
                target_device,
                bool(payload.get("sensitive", False)),
            )
            SESSION_LAST_JOBS[session_id] = job["id"]
            ROUTER_LOGGER.log_prompt(
                session_id,
                job["id"],
                message,
                sensitive=bool(payload.get("sensitive", False)),
            )
            POLICY.audit_event("job_created", session_id, job["id"], "queued")
            threading.Thread(
                target=_run_chat_job,
                args=(job["id"], payload.get("approvals"), bool(payload.get("sensitive", False)), target_device),
                daemon=True,
            ).start()
            self._json({"response": "", "job": _ask_job_payload(job)}, 202)
        elif path == "/api/stop":
            run_id = str(self._body().get("run_id", ""))
            event = RUN_CANCEL_EVENTS.get(run_id)
            if event:
                event.set()
            self._json({"success": True, "stopped": bool(event), "run_id": run_id})
        elif path == "/api/emergency-stop":
            POLICY.stop_all()
            self._json({"success": True, "stopped": True})
        elif path == "/api/resume":
            POLICY.reset_stop()
            self._json({"success": True, "stopped": False})
        elif path == "/api/mode":
            global MODE
            selected = str(self._body().get("mode", "")).strip().lower()
            if selected not in {"jarvis", "friday", "autopilot"}:
                self._json({"error": "Mode must be jarvis, friday, or autopilot."}, 400)
                return
            MODE = selected
            self._json({"mode": MODE})
        elif path == "/api/jobs":
            payload = self._body()
            try:
                job = JOBS.create(payload.get("steps", []))
            except ValueError as error:
                self._json({"error": str(error)}, 400)
                return
            JOBS.run_async(job["id"], lambda step: {"step": step, "status": "checkpointed"})
            self._json({"job": JOBS.get(job["id"])}, 201)
        elif path.startswith("/api/jobs/") and path.endswith("/cancel"):
            job_id = path.removeprefix("/api/jobs/").removesuffix("/cancel")
            self._json({"success": JOBS.cancel(job_id)})
        elif path.startswith("/api/ask-jobs/") and path.endswith("/answer"):
            job_id = path.removeprefix("/api/ask-jobs/").removesuffix("/answer")
            payload = self._body()
            answer = str(payload.get("answer", ""))
            try:
                pending = ASK_JOBS.get(job_id)
                if pending is None:
                    raise KeyError("Question job not found.")
                if pending["state"] in {"waiting_for_approval", "permission_request"}:
                    job, approved = ASK_JOBS.answer_approval(
                        job_id,
                        str(payload.get("approval_id", "")),
                        answer,
                    )
                    if approved is None:
                        self._json({"job": _ask_job_payload(job)}, 200)
                        return
                    POLICY.audit_event(
                        "approval_answered",
                        job["session_id"],
                        job_id,
                        "approved" if approved else "declined",
                    )
                    threading.Thread(
                        target=_resume_approval_job,
                        args=(job_id, approved),
                        daemon=True,
                    ).start()
                    self._json({"job": _ask_job_payload(job)}, 202)
                    return
                job = ASK_JOBS.answer(job_id, answer)
            except (KeyError, ValueError) as error:
                self._json({"error": str(error)}, 404 if isinstance(error, KeyError) else 400)
                return
            POLICY.audit_event("user_answered", job["session_id"], job_id, "running")
            threading.Thread(target=_resume_ask_job, args=(job_id,), daemon=True).start()
            self._json({"job": _ask_job_payload(job)}, 202)
        elif path == "/api/notifications/clear":
            deleted = NOTIFIER.clear_all()
            self._json({"success": True, "deleted": deleted, "unread": 0})
        elif path == "/api/notifications/settings":
            payload = self._body()
            updates = payload.get("settings", payload) if isinstance(payload, dict) else {}
            updated = NOTIFIER.update_settings(updates)
            if "camera_motion" in updates:
                active = bool(updates["camera_motion"])
                if active:
                    POLICY.audit_event("motion_watch", "system", "motion_monitor", "enabled")
                    MOTION_MONITOR.on_event = _notify_motion_event
                    MOTION_MONITOR.start()
                else:
                    POLICY.audit_event("motion_watch", "system", "motion_monitor", "disabled")
                    MOTION_MONITOR.stop()
            self._json({
                "settings": updated,
                "temperature": NOTIFIER.get_temperature(),
                "motion_active": MOTION_MONITOR.health_check().get("running", False),
            })
        elif path == "/api/notifications/read":
            payload = self._body()
            notification_id = payload.get("id")
            try:
                count = NOTIFIER.mark_read(int(notification_id)) if notification_id is not None else NOTIFIER.mark_read()
            except (TypeError, ValueError):
                self._json({"error": "Notification id must be an integer."}, 400)
                return
            notifications, unread = NOTIFIER.list_notifications()
            self._json({"updated": count, "notifications": notifications, "unread": unread})
        elif path == "/api/notifications/webpush/subscribe":
            payload = self._body()
            try:
                subscription = NOTIFIER.subscribe(
                    payload.get("subscription", {}),
                    str(payload.get("device_name", "")),
                )
            except (TypeError, ValueError) as error:
                self._json({"error": str(error)}, 400)
                return
            self._json({"subscription": subscription}, 201)
        elif path == "/api/notifications/webpush/activity":
            payload = self._body()
            if not NOTIFIER.touch_subscription(str(payload.get("id", ""))):
                self._json({"error": "Subscription not found."}, 404)
                return
            self._json({"success": True})
        elif path == "/api/notifications/webpush/test":
            try:
                NOTIFIER.send_test()
            except (OSError, PermissionError, RuntimeError, ValueError) as error:
                self._json({"error": str(error)}, 503 if isinstance(error, RuntimeError) else 400)
                return
            self._json({"success": True})
        elif path == "/api/improvements":
            payload = self._body()
            try:
                proposal = IMPROVEMENTS.create(str(payload.get("title", "")), str(payload.get("rationale", "")))
            except ValueError as error:
                self._json({"error": str(error)}, 400)
                return
            self._json({"proposal": proposal}, 201)
        elif path == "/api/devices/pair":
            payload = self._body()
            try:
                device = DEVICES.pair(
                    str(payload.get("id", "")),
                    str(payload.get("name", "")),
                    str(payload.get("device_type", "")),
                    payload.get("capabilities", []),
                    self.headers.get("X-HSX-Owner-PIN", ""),
                )
            except (TypeError, ValueError, PermissionError) as error:
                self._json({"error": str(error)}, 400 if isinstance(error, (TypeError, ValueError)) else 403)
                return
            self._json({"device": device}, 201)
        elif path.startswith("/api/devices/") and path.endswith("/authorize"):
            device_id = path.removeprefix("/api/devices/").removesuffix("/authorize")
            payload = self._body()
            try:
                result = DEVICES.authorize(
                    device_id,
                    self.headers.get("X-HSX-Device-Token", ""),
                    str(payload.get("capability", "")),
                )
            except PermissionError as error:
                self._json({"error": str(error)}, 403)
                return
            self._json(result)
        elif path.startswith("/api/improvements/") and path.endswith("/prototype"):
            proposal_id = path.removeprefix("/api/improvements/").removesuffix("/prototype")
            payload = self._body()
            try:
                proposal = IMPROVEMENTS.prototype(
                    proposal_id, str(payload.get("path", "")), str(payload.get("content", ""))
                )
            except (KeyError, ValueError) as error:
                self._json({"error": str(error)}, 400)
                return
            self._json({"proposal": proposal})
        elif path.startswith("/api/improvements/") and path.endswith("/check"):
            proposal_id = path.removeprefix("/api/improvements/").removesuffix("/check")
            try:
                proposal = IMPROVEMENTS.run_strict_checks(proposal_id)
            except (KeyError, ValueError, OSError) as error:
                self._json({"error": str(error)}, 400)
                return
            self._json({"proposal": proposal})
        elif path.startswith("/api/improvements/") and path.endswith("/approve"):
            proposal_id = path.removeprefix("/api/improvements/").removesuffix("/approve")
            try:
                proposal = IMPROVEMENTS.approve(
                    proposal_id, self.headers.get("X-HSX-Owner-PIN", "")
                )
            except (KeyError, ValueError, PermissionError) as error:
                self._json({"error": str(error)}, 403)
                return
            self._json({"proposal": proposal})
        elif path.startswith("/api/improvements/") and path.endswith("/promote"):
            proposal_id = path.removeprefix("/api/improvements/").removesuffix("/promote")
            payload = self._body()
            target_name = Path(str(payload.get("target", "")))
            if not target_name.parts or target_name.parts[0] not in {"agent", "skills", "interfaces", "prompts"}:
                self._json({"error": "Promotion targets must be under agent, skills, interfaces, or prompts."}, 400)
                return
            try:
                proposal = IMPROVEMENTS.promote(
                    proposal_id,
                    str(payload.get("path", "")),
                    BASE_DIR / target_name,
                    self.headers.get("X-HSX-Owner-PIN", ""),
                )
            except (KeyError, ValueError, PermissionError) as error:
                self._json({"error": str(error)}, 403)
                return
            self._json({"proposal": proposal})
        elif path == "/api/chat/stream":
            payload = self._body()
            message = str(payload.get("message", "")).strip()
            if not message:
                self._json({"error": "Message is required."}, 400)
                return
            client_message_id = str(payload.get("client_message_id", "")).strip()
            if client_message_id and (
                len(client_message_id) > 128
                or not client_message_id.isascii()
                or any(not (char.isalnum() or char in "-_.:") for char in client_message_id)
            ):
                self._json({"error": "client_message_id must contain 1 to 128 safe ASCII characters."}, 400)
                return
            try:
                last_event_id = int(self.headers.get("Last-Event-ID", "0"))
            except ValueError:
                self._json({"error": "Last-Event-ID must be an integer."}, 400)
                return
            if last_event_id < 0:
                self._json({"error": "Last-Event-ID cannot be negative."}, 400)
                return
            feedback = message.lower()
            if feedback in {"/good", "/bad"}:
                session_id = str(payload.get("session_id", "default"))[:100]
                job_id = SESSION_LAST_JOBS.get(session_id)
                if job_id is None:
                    self._json({"error": "There is no recent task to rate."}, 404)
                    return
                ROUTER_LOGGER.log_feedback(session_id, job_id, feedback[1:])
                record_provider_feedback(job_id, feedback[1:])
                self._json({"response": "Thanks for the feedback.", "feedback": feedback[1:]})
                return
            try:
                target_device = resolve_target_device(
                    str(payload.get("target_device", "client_browser")),
                    self.client_address[0],
                    allow_remote_local_host=getattr(self, "_authorized_remote_control", False),
                )
            except (ValueError, PermissionError) as error:
                self._json({"error": str(error)}, 400 if isinstance(error, ValueError) else 403)
                return
            session_id = str(payload.get("session_id", "default"))[:100]
            job = (
                ASK_JOBS.get_by_client_message(session_id, client_message_id)
                if client_message_id
                else None
            )
            if job and job["request"] != message:
                self._json({"error": "client_message_id was already used for a different request."}, 409)
                return
            created = job is None
            if job is None:
                try:
                    job, created = ASK_JOBS.create_or_get_running(
                        message,
                        session_id,
                        target_device=target_device,
                        sensitive=bool(payload.get("sensitive", False)),
                        client_message_id=client_message_id,
                    )
                except ValueError as error:
                    self._json({"error": str(error)}, 400)
                    return
            if created:
                try:
                    task_directory = WORKSPACE.create_task_dir(message).relative_to(WORK_DIR).as_posix()
                    job = ASK_JOBS.update_task_directory(job["id"], task_directory)
                except (KeyError, OSError, RuntimeError, ValueError) as error:
                    ASK_JOBS.fail(job["id"], f"Could not create the task folder: {error}")
                    POLICY.audit_event("job_failed", session_id, job["id"], "task_folder")
                    self._json({"error": "Could not create a task folder for this request."}, 500)
                    return
                cancel_event = threading.Event()
                RUN_CANCEL_EVENTS[job["id"]] = cancel_event
                event_queue = CHAT_STREAMS.register(job["id"])
                _emit_chat_event(job["id"], "message_start", {"run_id": job["id"]})
                _emit_chat_event(job["id"], "job", {"id": job["id"], "state": "running"})
                threading.Thread(
                    target=_run_chat_job,
                    args=(
                        job["id"],
                        payload.get("approvals"),
                        bool(payload.get("sensitive", False)),
                        target_device,
                        lambda text: _emit_chat_event(job["id"], "text_delta", {"text": text}),
                        cancel_event,
                        lambda notice: _emit_chat_event(
                            job["id"], "provider_notice", {"message": notice}
                        ),
                        lambda event, data: _emit_chat_event(job["id"], event, data),
                    ),
                    daemon=True,
                ).start()
                replay = []
                stream_finished = False
            else:
                event_queue, replay, stream_finished = CHAT_STREAMS.subscribe(job["id"], last_event_id)
            if event_queue is None and job["state"] == "running":
                self._json(
                    {"error": "The live chat stream is unavailable; check the task status before retrying."},
                    503,
                )
                return
            SESSION_LAST_JOBS[session_id] = job["id"]
            if created:
                ROUTER_LOGGER.log_prompt(
                    session_id,
                    job["id"],
                    message,
                    sensitive=bool(payload.get("sensitive", False)),
                )
                POLICY.audit_event("job_created", session_id, job["id"], "queued")
            self.send_response(200)
            self._security_headers()
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self._cors_headers()
            self.end_headers()

            def write_event(event_id: int, event_name: str, event_data: dict) -> None:
                encoded = json.dumps(event_data, separators=(",", ":"))
                self.wfile.write(f"id: {event_id}\nevent: {event_name}\ndata: {encoded}\n\n".encode())

            try:
                seen_done = False
                if event_queue is None:
                    write_event(last_event_id + 1, "message_start", {"run_id": job["id"]})
                    write_event(last_event_id + 2, "job", _ask_job_payload(job))
                    write_event(last_event_id + 3, "done", {})
                    seen_done = True
                else:
                    for stream_event in replay:
                        write_event(stream_event.id, stream_event.event, stream_event.data)
                        if stream_event.event == "done":
                            seen_done = True
                            break
                self.wfile.flush()
                while event_queue is not None and not seen_done and not stream_finished:
                    try:
                        stream_event = event_queue.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        continue
                    write_event(stream_event.id, stream_event.event, stream_event.data)
                    self.wfile.flush()
                    if stream_event.event == "done":
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                if event_queue is not None:
                    CHAT_STREAMS.remove(job["id"], event_queue)
                self.close_connection = True
        elif path == "/api/memory":
            payload = self._body()
            try:
                fact = MEMORY.add_fact(
                    str(payload.get("text", payload.get("fact", ""))),
                    str(payload.get("category", "general")),
                    pinned=bool(payload.get("pinned", False)),
                )
            except ValueError as error:
                self._json({"error": str(error)}, 400)
                return
            self._json({"fact": fact, "facts": MEMORY.list_facts()})
        elif path.startswith("/api/memory/"):
            try:
                fact_id = int(path.removeprefix("/api/memory/"))
                payload = self._body()
                fact = MEMORY.update_fact(
                    fact_id,
                    str(payload.get("text", "")),
                    str(payload.get("category", "general")),
                    bool(payload.get("pinned", False)),
                )
            except (ValueError, KeyError) as error:
                self._json({"error": str(error)}, 400 if isinstance(error, ValueError) else 404)
                return
            self._json({"fact": fact})
        elif path == "/api/upload":
            try:
                body_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json({"error": "Upload size is invalid."}, 400)
                return
            if body_length <= 0 or body_length > 6 * 1024 * 1024:
                self._json({"error": "Files must be 1 MB or smaller."}, 413)
                return
            payload = self._body()
            filename = Path(str(payload.get("filename", "upload.txt"))).name
            if "content_base64" in payload:
                encoded = payload["content_base64"]
                if not isinstance(encoded, str):
                    self._json({"error": "File data is invalid."}, 400)
                    return
                try:
                    content = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error):
                    self._json({"error": "File data is invalid."}, 400)
                    return
            else:
                content = str(payload.get("content", "")).encode("utf-8")
            if len(content) > 1024 * 1024:
                self._json({"error": "Files must be 1 MB or smaller."}, 413)
                return
            if not filename:
                self._json({"error": "A file is required."}, 400)
                return
            try:
                destination = WORKSPACE.unique_write_path(Path("Uploads") / filename)
                with destination.open("xb") as uploaded_file:
                    uploaded_file.write(content)
            except (OSError, ValueError) as error:
                self._json({"error": str(error)}, 400)
                return
            self._json({
                "success": True,
                "name": destination.name,
                "path": destination.relative_to(WORKSPACE.root).as_posix(),
                "type": destination.suffix[1:],
            })
        elif path == "/api/media/upload":
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json({"error": "Content-Length must be an integer."}, 400)
                return
            if length <= 0 or length > 5 * 1024 * 1024:
                self._json({"error": "Media upload must be between 1 byte and 5 MB."}, 413)
                return
            filename = Path(
                self.headers.get("X-Filename", "browser_capture.webm").replace("\\", "/")
            ).name
            if not filename or filename in {".", ".."}:
                self._json({"error": "A valid media filename is required."}, 400)
                return
            try:
                media = self.rfile.read(length)
                if len(media) != length:
                    raise ValueError("The media upload was incomplete.")
                destination = WORKSPACE.unique_write_path(Path("Browser Media") / filename)
                destination.write_bytes(media)
            except (OSError, ValueError) as error:
                self._json({"error": str(error)}, 400)
                return
            self._json({
                "success": True,
                "name": destination.name,
                "path": destination.relative_to(WORK_DIR).as_posix(),
                "bytes": len(media),
            }, 201)
        elif path == "/api/video/jobs":
            payload = self._body()
            project_id = str(payload.get("project_id", "project_video")).strip() or "project_video"
            prompt = str(payload.get("prompt", "")).strip()
            source_files = payload.get("source_files", [])
            if not prompt:
                self._json({"error": "A prompt is required for a video job."}, 400)
                return
            job = create_video_job(
                project_id=project_id,
                prompt=prompt,
                source_files=source_files if isinstance(source_files, list) else [source_files],
                output_filename=str(payload.get("output_filename", "edited_video.mp4")).strip() or "edited_video.mp4",
            )
            self._json({"job": job})
        else:
            self._json({"error": "Not found."}, 404)

    def do_OPTIONS(self) -> None:
        if not self._api_access_allowed():
            return
        self.send_response(204)
        self._security_headers()
        self.send_header("Cache-Control", "no-store")
        self._cors_headers()
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.end_headers()

    def do_DELETE(self) -> None:
        path = self._request_path()
        if path.startswith("/api/") and not self._api_access_allowed():
            return
        if path == "/api/notifications":
            deleted = NOTIFIER.clear_all()
            self._json({"success": True, "deleted": deleted, "unread": 0})
            return
        if path.startswith("/api/conversations/"):
            session_id = unquote(path.removeprefix("/api/conversations/"))[:100]
            if not CONVERSATIONS.delete(session_id):
                self._json({"error": "Conversation not found."}, 404)
                return
            self._json({"success": True})
            return
        if path.startswith("/api/notifications/webpush/subscriptions/"):
            subscription_id = path.removeprefix("/api/notifications/webpush/subscriptions/")
            if not NOTIFIER.unsubscribe(subscription_id):
                self._json({"error": "Subscription not found."}, 404)
                return
            self._json({"success": True, "subscriptions": NOTIFIER.list_subscriptions()})
            return
        if path.startswith("/api/notifications/"):
            try:
                notification_id = int(path.removeprefix("/api/notifications/"))
            except ValueError:
                self._json({"error": "Notification id must be an integer."}, 400)
                return
            count = NOTIFIER.mark_read(notification_id)
            self._json({"success": bool(count)})
            return
        if path.startswith("/api/devices/"):
            device_id = path.removeprefix("/api/devices/")
            try:
                DEVICES.revoke(device_id, self.headers.get("X-HSX-Owner-PIN", ""))
            except (KeyError, PermissionError) as error:
                self._json({"error": str(error)}, 404 if isinstance(error, KeyError) else 403)
                return
            self._json({"success": True, "devices": DEVICES.list()})
            return
        if not path.startswith("/api/memory/"):
            self._json({"error": "Not found."}, 404)
            return
        try:
            fact_id = int(path.removeprefix("/api/memory/"))
            MEMORY.delete_fact(fact_id)
        except ValueError:
            self._json({"error": "Memory id must be an integer."}, 400)
            return
        self._json({"success": True, "facts": MEMORY.list_facts()})

    def _recent_outputs(self) -> list:
        return _session_artifacts("")

    def _serve_file(self, target: Path, *, inline: bool, no_store: bool = False) -> None:
        size = target.stat().st_size
        start, end = 0, size - 1
        status = 200
        range_header = self.headers.get("Range", "")
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if not match or size == 0:
                self.send_error(416, "Invalid byte range")
                return
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
            elif last:
                start = max(0, size - int(last))
            if start >= size or end < start:
                self.send_error(416, "Invalid byte range")
                return
            status = 206
        length = end - start + 1
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        disposition = "inline" if inline else "attachment"
        self.send_response(status)
        self._security_headers()
        self._cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{quote(target.name)}")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if no_store:
            self.send_header("Cache-Control", "no-store")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with target.open("rb") as source:
            source.seek(start)
            remaining = length
            while remaining:
                chunk = source.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _serve_frontend(self) -> None:
        request_path = self.path.split("?", 1)[0]
        cleaned_path = request_path.removeprefix("/web/").removeprefix("/web").lstrip("/")
        if not cleaned_path:
            cleaned_path = "index.html"
        asset = (ROOT / "web" / cleaned_path).resolve()
        if ROOT / "web" not in asset.parents or not asset.is_file():
            self._json({"error": "Not found."}, 404)
            return
        page = asset.read_bytes()
        content_type = {
            ".css": "text/css",
            ".js": "text/javascript",
            ".html": "text/html",
            ".svg": "image/svg+xml",
            ".json": "application/json",
            ".webmanifest": "application/manifest+json",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(asset.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self._security_headers()
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if "text" in content_type or "javascript" in content_type else content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)


def create_http_server() -> ThreadingHTTPServer:
    try:
        return ThreadingHTTPServer((HOST, PORT), HsxHandler)
    except OSError as error:
        if error.errno == errno.EADDRINUSE:
            print(
                f"Hsx cannot start: port {PORT} is already in use. Set HSX_PORT to a free port "
                "or stop the process using it.",
                file=sys.stderr,
            )
            raise SystemExit(2) from error
        raise


if __name__ == "__main__":
    http_server = create_http_server()
    if GROQ_API_KEY:
        try:
            GroqProvider(GROQ_API_KEY, GROQ_MODEL)
        except ProviderError as error:
            LOGGER.warning("Could not validate the Groq model at startup: %s", error)
    if os.getenv("HSX_ENABLE_SYSTEM_MONITOR", "1").strip().lower() not in {"0", "false", "off", "no"}:
        ANOMALY_MONITOR.on_sample = _notify_resource_alerts
        ANOMALY_MONITOR.on_anomaly = _notify_system_anomaly
        ANOMALY_MONITOR.start()
    if MOTION_MONITOR.enabled():
        MOTION_MONITOR.on_event = _notify_motion_event
        MOTION_MONITOR.start()
    print(f"Hsx ready. Loaded {len(TOOLS)} skills: {', '.join(tool['name'] for tool in TOOLS)}")
    http_server.serve_forever()
