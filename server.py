import base64
import binascii
import json
import logging
import mimetypes
import os
import queue
import re
import shutil
import sqlite3
import threading
import time
import copy
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from api.chat import ChatStreamRegistry
from api.devices import resolve_target_device
from api.files import resolve_output
from api.jobs import ask_job_payload
from agent.ask_user import ApprovalRequest, AskUserJobs, AskUserRequest
from agent.jobs import JobQueue
from agent.approval import ApprovalGuard
from agent.devices import DeviceRegistry, system_body_state
from agent.feedback import FeedbackStore
from agent.hardware import AUDIO_ALERTS, CAMERA, MOTION_MONITOR
from agent.hardware.camera import MAX_FRAME_AGE_SECONDS
from agent.llm.router import default_router
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
from agent.notifier import Notifier
from agent.orchestrator import run_agent
from agent.policy import POLICY
from agent.router import INTENT_ROUTER, LLM_TELEMETRY, ROUTER_LOGGER
from agent.self_improvement import SelfImprovement
from agent.system_tools import system_info
from agent.skill_loader import load_skills, set_runtime_registry
from agent.tools.dynamic_registry import DYNAMIC_TOOLS, register_meta_tool
from agent.workspace import Workspace, task_output_scope, workspace_search
from config import (
    DATA_DIR,
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
TOOLS, SKILL_MAP = load_skills(str(ROOT / "skills"))
try:
    DYNAMIC_TOOLS.restore(TOOLS, SKILL_MAP)
    register_meta_tool(TOOLS, SKILL_MAP)
except (OSError, RuntimeError, TypeError, ValueError):
    LOGGER.exception("Could not restore the constrained dynamic tool registry.")
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
NOTIFIER = Notifier(DATA_DIR / "notifications.db")
FEEDBACK_STORE = FeedbackStore(DATA_DIR / "feedback.db")
_RESOURCE_ALERT_LOCK = threading.Lock()
_RESOURCE_ALERT_STATE = {"cpu": False, "memory": False, "disk": False}
MESH_GATEWAY = MeshGateway.from_environment()


def _hardware_audio_alert(*, succeeded: bool, message: str = "") -> None:
    try:
        if succeeded:
            AUDIO_ALERTS.notify_success(message or "Hsx task completed successfully.")
        else:
            AUDIO_ALERTS.play_async("warning")
    except (OSError, RuntimeError, ValueError):
        LOGGER.exception("Could not queue a hardware audio alert.")


def _emit_chat_event(job_id: str, event: str, payload: dict) -> None:
    CHAT_STREAMS.emit(job_id, event, payload)



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
        NOTIFIER.notify(
            "camera_motion",
            "Motion detected by the Hsx host camera",
            dedupe_key=f"camera-motion:{snapshot}",
        )


def _notify_resource_alerts(metrics: SystemMetrics) -> None:
    disk = shutil.disk_usage(DATA_DIR)
    values = {
        "cpu": (metrics.cpu_percent >= 95, "CPU use is unusually high on the Hsx host"),
        "memory": (metrics.ram_percent >= 90, "Memory use is unusually high on the Hsx host"),
        "disk": (
            disk.used / max(1, disk.total) >= 0.85,
            "Hsx host storage is at least 85% full",
        ),
    }
    new_alerts = []
    with _RESOURCE_ALERT_LOCK:
        for resource, (is_high, title) in values.items():
            if is_high and not _RESOURCE_ALERT_STATE[resource]:
                new_alerts.append((resource, title))
            _RESOURCE_ALERT_STATE[resource] = is_high
    for resource, title in new_alerts:
        NOTIFIER.notify("health_alert", title, dedupe_key=f"resource:{resource}:{time.time_ns()}")


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


def _camera_status_payload() -> dict:
    status = CAMERA.health_check()
    host = system_info()
    return {
        **status,
        "host": {
            "hostname": host["hostname"],
            "hardware": host["cpu"]["model"],
        },
    }


def _session_artifacts(session_id: str) -> list[dict]:
    if not session_id:
        return []
    work_root = WORK_DIR.resolve()
    files = []
    for task_dir in ASK_JOBS.artifact_directories(session_id):
        try:
            relative_task_dir = WORKSPACE._relative_path(task_dir)
            task_root = (work_root / relative_task_dir).resolve()
            WORKSPACE._assert_inside(work_root, task_root)
            if not task_root.is_dir():
                continue
            directory = task_root.relative_to(WORKSPACE.root).as_posix()
            files.extend(
                item
                for item in workspace_search(WORKSPACE, directory=directory, limit=200)
                if not _is_user_upload_path(item["path"])
            )
        except (OSError, ValueError):
            LOGGER.warning("Skipping invalid artifact directory for session %s.", session_id)
    unique = {item["path"]: item for item in files}
    return sorted(unique.values(), key=lambda item: (item["modified"], item["path"]), reverse=True)[:200]


def _fail_job(
    job_id: str,
    job: dict,
    error: Exception,
    *,
    stream: bool = False,
    started_at: float | None = None,
) -> None:
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
    NOTIFIER.notify("job_failed", "Hsx could not complete the task", job_id=job_id)


def _resume_ask_job(job_id: str) -> None:
    job = ASK_JOBS.get(job_id)
    if job is None or job["state"] != "running":
        return
    lock = _session_lock(job["session_id"])
    started_at = time.perf_counter()
    device_actions = list(job.get("device_actions") or [])
    resume_history = copy.deepcopy(job["checkpoint"])
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
        )
        SESSION_HISTORIES[job["session_id"]] = approval.checkpoint
        ASK_JOBS.save_device_actions(job_id, device_actions)
        _emit_chat_event(job_id, "job", _ask_job_payload(updated))
        POLICY.audit_event("approval_requested", job["session_id"], job_id, approval.approval_request["risk"])
        NOTIFIER.notify("needs_approval", "Hsx needs approval", job_id=job_id)
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
        NOTIFIER.notify("job_failed", "Hsx could not complete the task", job_id=job_id)
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
    POLICY.audit_event("job_done", job["session_id"], job_id, "done")
    _hardware_audio_alert(succeeded=True)
    NOTIFIER.notify(
        "job_done",
        "Your Hsx task is complete",
        job_id=job_id,
        result_path=_task_result_path(job),
    )


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
    if not isinstance(arguments, dict) or tool_name not in SKILL_MAP:
        _fail_job(job_id, job, RuntimeError("The stored approval request is invalid."), started_at=started_at)
        return
    skill = next((item for item in TOOLS if item["name"] == tool_name), None)
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
    if not policy_result["allowed"]:
        result = {
            "success": False,
            "error": "The user declined this action."
            if not approved
            else "The action is blocked by the host safety policy.",
        }
    else:
        tool_input = dict(arguments)
        if tool_name in {"audio_play", "camera_record"}:
            tool_input["target_device"] = job.get("target_device", "local_host")
        try:
            result = SKILL_MAP[tool_name](**tool_input)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            result = {"success": False, "error": str(error)}
        if isinstance(result, dict) and tool_name in {"audio_play", "camera_record"}:
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
        )
        SESSION_HISTORIES[job["session_id"]] = next_approval.checkpoint
        ASK_JOBS.save_device_actions(job_id, device_actions)
        _emit_chat_event(job_id, "job", _ask_job_payload(updated))
        POLICY.audit_event(
            "approval_requested",
            job["session_id"],
            job_id,
            next_approval.approval_request["risk"],
        )
        NOTIFIER.notify("needs_approval", "Hsx needs approval", job_id=job_id)
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
            NOTIFIER.notify("job_failed", "Hsx could not complete the task", job_id=job_id)
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
    NOTIFIER.notify("job_done", "Your Hsx task is complete", job_id=job_id)


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
) -> None:
    job = ASK_JOBS.get(job_id)
    if job is None or job["state"] != "running":
        return
    lock = _session_lock(job["session_id"])
    started_at = time.perf_counter()
    POLICY.audit_event("job_started", job["session_id"], job_id, "running")
    device_actions = list(job.get("device_actions") or [])
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
        )
        SESSION_HISTORIES[job["session_id"]] = approval.checkpoint
        ASK_JOBS.save_device_actions(job_id, device_actions)
        _emit_chat_event(job_id, "job", _ask_job_payload(updated))
        _emit_chat_event(job_id, "done", {})
        if updated["state"] != "waiting_for_approval":
            return
        POLICY.audit_event(
            "approval_requested",
            job["session_id"],
            job_id,
            approval.approval_request["risk"],
        )
        NOTIFIER.notify("needs_approval", "Hsx needs approval", job_id=job_id)
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
            NOTIFIER.notify("job_failed", "Hsx could not complete the task", job_id=job_id)
        return
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as error:
        _fail_job(job_id, job, error, stream=True, started_at=started_at)
        return
    except Exception as error:
        _fail_job(job_id, job, error, stream=True, started_at=started_at)
        return
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
    NOTIFIER.notify(
        "job_done",
        "Your Hsx task is complete",
        job_id=job_id,
        result_path=_task_result_path(job),
    )


class HsxHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        LOGGER.info("HTTP %s %s", self.client_address[0], format % args)

    def _security_headers(self) -> None:
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; connect-src 'self'; "
            "font-src 'self' data:; form-action 'self'; "
            "img-src 'self' data: blob:; media-src 'self' blob:; "
            "object-src 'none'; script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(self), microphone=(self)")

    def _request_path(self) -> str:
        return urlsplit(self.path).path

    def _api_access_allowed(self) -> bool:
        self._authorized_remote_control = True
        return True

    def _json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin")
        if origin and origin != "null":
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
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
        origin = self.headers.get("Origin")
        if origin and origin != "null":
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        try:
            while True:
                notifications = NOTIFIER.wait_for_notifications(last_id, timeout=15)
                if not notifications:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                for notification in notifications:
                    data = json.dumps(notification, separators=(",", ":"))
                    self.wfile.write(f"id: {notification['id']}\nevent: notification\ndata: {data}\n\n".encode())
                    last_id = notification["id"]
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def _stream_camera(self) -> None:
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
        if not self._api_access_allowed():
            return
        query = parse_qs(urlsplit(self.path).query)
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
        elif path == "/health":
            self._json({"status": "ok"})
        elif path == "/api/skills":
            self._json({"skills": [tool["name"] for tool in TOOLS]})
        elif path == "/api/policy/status":
            self._json({"stopped": POLICY.is_stopped()})
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
            if not CAMERA.is_started() and CAMERA.health_check().get("device_available"):
                CAMERA.start()
            self._json(_camera_status_payload())
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
            self._json({"notifications": notifications, "unread": unread})
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
            if session_id:
                files = _session_artifacts(session_id)
            else:
                files = [
                    item
                    for item in workspace_search(
                        WORKSPACE,
                        directory=WORK_DIR.relative_to(WORKSPACE_DIR),
                        limit=200,
                    )
                    if not _is_user_upload_path(item["path"])
                ]
            files = [
                {"name": item["name"], "path": item["path"], "type": item["type"]}
                for item in files
            ]
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
        if path == "/api/mesh/tasks":
            path = "/api/chat/stream"
        if path == "/api/feedback/settings":
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
                telemetry = LLM_TELEMETRY.snapshot()
                tool_names = [
                    str(item.get("type"))
                    for item in job.get("device_actions", [])
                    if isinstance(item, dict) and item.get("type")
                ]
                prompt_messages = payload.get("prompt_messages") or [{
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
                        provider=str(telemetry.get("provider", "")),
                        model=str(telemetry.get("model", "")),
                        tool_names=tool_names,
                        app_version=os.getenv("HSX_APP_VERSION", "0.0.0+local"),
                        client=str(payload.get("client", "web")),
                        prompt_messages=prompt_messages,
                        response=str(payload.get("response", job.get("result", ""))),
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
                if pending["state"] == "waiting_for_approval":
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
            cancel_event = threading.Event()
            RUN_CANCEL_EVENTS[job["id"]] = cancel_event
            SESSION_LAST_JOBS[session_id] = job["id"]
            ROUTER_LOGGER.log_prompt(
                session_id,
                job["id"],
                message,
                sensitive=bool(payload.get("sensitive", False)),
            )
            POLICY.audit_event("job_created", session_id, job["id"], "queued")
            event_queue = CHAT_STREAMS.register(job["id"])
            threading.Thread(
                target=_run_chat_job,
                args=(
                    job["id"],
                    payload.get("approvals"),
                    bool(payload.get("sensitive", False)),
                    target_device,
                    lambda text: _emit_chat_event(job["id"], "text_delta", {"text": text}),
                    cancel_event,
                ),
                daemon=True,
            ).start()
            self.send_response(200)
            self._security_headers()
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            try:
                initial_events = (
                    f"event: message_start\ndata: {json.dumps({'run_id': job['id']})}\n\n"
                    f"event: job\ndata: {json.dumps({'id': job['id'], 'state': 'running'})}\n\n"
                )
                self.wfile.write(initial_events.encode())
                self.wfile.flush()
                while True:
                    try:
                        event_name, event_data = event_queue.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        continue
                    data = json.dumps(event_data, separators=(",", ":"))
                    self.wfile.write(f"event: {event_name}\ndata: {data}\n\n".encode())
                    self.wfile.flush()
                    if event_name == "done":
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                CHAT_STREAMS.remove(job["id"])
                RUN_CANCEL_EVENTS.pop(job["id"], None)
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
        origin = self.headers.get("Origin")
        if origin and origin != "null":
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.end_headers()

    def do_DELETE(self) -> None:
        path = self._request_path()
        if path.startswith("/api/") and not self._api_access_allowed():
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
        return [
            {"name": item["name"], "path": item["path"], "type": item["type"]}
            for item in workspace_search(
                WORKSPACE,
                directory=WORK_DIR.relative_to(WORKSPACE_DIR),
                limit=100,
            )
        ]

    def _serve_file(self, target: Path, *, inline: bool) -> None:
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
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{quote(target.name)}")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
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


if __name__ == "__main__":
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
    ThreadingHTTPServer((HOST, PORT), HsxHandler).serve_forever()
