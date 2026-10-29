import importlib.util
import json
import queue
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agent.workspace import Workspace
from config import MEMORY_DIR, OUTPUTS_DIR, SCRATCH_DIR, WORKSPACE_DIR

JOB_STORE_PATH = MEMORY_DIR / "video_jobs.json"
_JOB_LOCK = threading.Lock()
_JOB_QUEUE = queue.Queue()
_JOB_STATE = {}
_BACKGROUND_WORKER = None
WORKSPACE = Workspace(WORKSPACE_DIR, OUTPUTS_DIR)


def _module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def inspect_video_tooling() -> dict:
    return {
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "moviepy": _module_available("moviepy"),
        "whisper": _module_available("whisper"),
    }


def _load_jobs() -> dict:
    if not JOB_STORE_PATH.exists():
        return {}
    try:
        return json.loads(JOB_STORE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_jobs(jobs: dict) -> None:
    JOB_STORE_PATH.write_text(json.dumps(jobs, indent=2), encoding="utf-8")


def _resolve_source_files(source_files: list | None) -> list[Path]:
    if not source_files:
        return []

    candidates = []
    for raw_path in source_files:
        try:
            resolved = WORKSPACE.resolve_read(raw_path)
        except ValueError:
            continue
        if resolved.is_file():
            candidates.append(resolved)
    return candidates


def _remember_video_edit(summary: str, output_path: str, prompt: str, files: list[str]) -> None:
    facts_path = MEMORY_DIR / "facts.json"
    facts = []
    if facts_path.exists():
        try:
            facts = json.loads(facts_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            facts = []

    facts.append({
        "kind": "video_edit",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "prompt": prompt,
        "source_files": files,
        "output_path": output_path,
    })
    facts = facts[-40:]
    facts_path.write_text(json.dumps(facts, indent=2), encoding="utf-8")


def build_edit_plan(
    project_id: str,
    prompt: str,
    source_files: list[str] | None = None,
    output_filename: str = "edited_video.mp4",
    trim_start: float | None = None,
    trim_end: float | None = None,
) -> dict:
    lowered = (prompt or "").lower()
    style = {
        "captions": "caption" in lowered or "subtitle" in lowered,
        "trim_silence": "silence" in lowered or "dead air" in lowered or "quiet" in lowered,
        "highlight_reel": "highlight" in lowered or "teaser" in lowered,
        "intro_title": "intro" in lowered or "title" in lowered,
    }

    clips = []
    for item in source_files or []:
        clip = {"source": str(item), "in": 0.0, "out": None, "speed": 1.0}
        if trim_start is not None:
            clip["in"] = trim_start
        if trim_end is not None:
            clip["out"] = trim_end
        clips.append(clip)

    return {
        "project_id": project_id,
        "prompt": prompt,
        "timeline": {
            "video_tracks": [{"type": "video", "clips": clips}],
            "audio_tracks": [],
        },
        "render": {
            "format": "mp4",
            "resolution": "1920x1080",
            "fps": 30,
            "output_filename": output_filename,
        },
        "style": style,
    }


def validate_edit_plan(plan: dict) -> bool:
    if not isinstance(plan, dict):
        return False
    for key in ("project_id", "timeline", "render", "style"):
        if key not in plan:
            return False
    if not isinstance(plan["timeline"], dict):
        return False
    return "video_tracks" in plan["timeline"] or "audio_tracks" in plan["timeline"]


def _ensure_background_worker() -> None:
    global _BACKGROUND_WORKER
    if _BACKGROUND_WORKER is not None and _BACKGROUND_WORKER.is_alive():
        return
    _BACKGROUND_WORKER = threading.Thread(target=_process_job_queue, daemon=True)
    _BACKGROUND_WORKER.start()


def create_video_job(
    project_id: str,
    prompt: str,
    source_files: list | None = None,
    output_filename: str = "edited_video.mp4",
    trim_start: float | None = None,
    trim_end: float | None = None,
) -> dict:
    output_directory = WORKSPACE.create_task_dir(project_id).relative_to(OUTPUTS_DIR).as_posix()
    plan = build_edit_plan(project_id, prompt, source_files, output_filename, trim_start, trim_end)
    plan["render"]["output_directory"] = output_directory
    job_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    job = {
        "job_id": job_id,
        "project_id": project_id,
        "prompt": prompt,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "edit_plan": plan,
        "result": None,
        "error": None,
    }

    with _JOB_LOCK:
        jobs = _load_jobs()
        jobs[job_id] = job
        _save_jobs(jobs)
        _JOB_STATE[job_id] = job

    _JOB_QUEUE.put(job_id)
    _ensure_background_worker()
    return job


def get_video_job(job_id: str) -> dict | None:
    with _JOB_LOCK:
        jobs = _load_jobs()
        job = jobs.get(job_id)
        if job is not None:
            _JOB_STATE[job_id] = job
        return job


def list_video_jobs() -> list[dict]:
    with _JOB_LOCK:
        jobs = _load_jobs()
        return [jobs[key] for key in sorted(jobs.keys(), reverse=True)]


def _process_job_queue() -> None:
    while True:
        try:
            job_id = _JOB_QUEUE.get(timeout=1)
        except queue.Empty:
            continue

        try:
            with _JOB_LOCK:
                jobs = _load_jobs()
                job = jobs.get(job_id)
                if job is None:
                    continue
                job["status"] = "running"
                job["updated_at"] = datetime.now(timezone.utc).isoformat()
                _save_jobs(jobs)
                _JOB_STATE[job_id] = job

            result = execute_edit_plan(job["edit_plan"])

            with _JOB_LOCK:
                jobs = _load_jobs()
                job = jobs.get(job_id)
                if not job:
                    continue
                job["status"] = "completed" if result.get("success") else "failed"
                job["result"] = result
                job["error"] = None if result.get("success") else result.get("error")
                job["updated_at"] = datetime.now(timezone.utc).isoformat()
                _save_jobs(jobs)
                _JOB_STATE[job_id] = job
        except Exception as exc:  # pragma: no cover - safety net for worker failures
            with _JOB_LOCK:
                jobs = _load_jobs()
                job = jobs.get(job_id)
                if job is not None:
                    job["status"] = "failed"
                    job["error"] = str(exc)
                    job["updated_at"] = datetime.now(timezone.utc).isoformat()
                    _save_jobs(jobs)
                    _JOB_STATE[job_id] = job
        finally:
            _JOB_QUEUE.task_done()


def _build_plan(prompt: str, source_files: list[Path]) -> dict:
    lowered = prompt.lower()
    if "transcrib" in lowered:
        operation = "transcribe"
    elif "concat" in lowered or "merge" in lowered or len(source_files) > 1:
        operation = "concat"
    elif "trim" in lowered or "cut" in lowered or "clip" in lowered:
        operation = "trim"
    else:
        operation = "pass-through"

    return {
        "operation": operation,
        "prompt": prompt,
        "source_files": [str(path) for path in source_files],
    }


def _ffmpeg_concat(output_path: Path, input_paths: list[Path]) -> None:
    list_file = SCRATCH_DIR / f"concat_{uuid.uuid4().hex}.txt"
    with list_file.open("w", encoding="utf-8") as handle:
        for item in input_paths:
            handle.write(f"file '{item.as_posix()}'\n")

    subprocess.run(
        ["ffmpeg", "-n", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(output_path)],
        check=True,
        capture_output=True,
        text=True,
        cwd=str(SCRATCH_DIR),
    )
    list_file.unlink(missing_ok=True)


def _ffmpeg_trim(output_path: Path, input_path: Path, start_seconds: float | None = None, end_seconds: float | None = None) -> None:
    command = ["ffmpeg", "-n", "-i", str(input_path)]
    if start_seconds is not None:
        command.extend(["-ss", str(start_seconds)])
    if end_seconds is not None:
        command.extend(["-to", str(end_seconds)])
    command.extend(["-c", "copy", str(output_path)])
    subprocess.run(command, check=True, capture_output=True, text=True, cwd=str(SCRATCH_DIR))


def _transcribe_video(input_path: Path) -> str:
    if not _module_available("whisper"):
        raise RuntimeError("Whisper is not installed. Install openai-whisper or skip transcription.")

    import whisper

    model = whisper.load_model("base")
    result = model.transcribe(str(input_path))
    return result.get("text", "")


def execute_edit_plan(plan: dict) -> dict:
    if not validate_edit_plan(plan):
        return {"success": False, "error": "The video edit plan is invalid."}

    prompt = str(plan.get("prompt", "")).strip()
    if not prompt:
        return {"success": False, "error": "A prompt is required for video editing."}

    status = inspect_video_tooling()
    if not status["ffmpeg"]:
        return {"success": False, "error": "FFmpeg is not installed or not on PATH.", "tooling": status}

    clips = []
    for track in plan.get("timeline", {}).get("video_tracks", []):
        for clip in track.get("clips", []):
            source = clip.get("source")
            if source:
                clips.append(source)

    if not clips:
        return {"success": False, "error": "No source clips were defined in the edit plan.", "tooling": status}

    resolved_files = _resolve_source_files(clips)
    if not resolved_files:
        return {"success": False, "error": "No valid source video files were found for the edit plan.", "tooling": status}

    output_filename = Path(plan.get("render", {}).get("output_filename", "edited_video.mp4")).name
    output_directory = plan.get("render", {}).get("output_directory")
    if not output_directory:
        output_directory = WORKSPACE.create_task_dir("video task").relative_to(OUTPUTS_DIR).as_posix()
    try:
        output_path = WORKSPACE.resolve_write(Path(output_directory) / output_filename)
    except ValueError as error:
        return {"success": False, "error": str(error), "tooling": status}
    output_path.parent.mkdir(exist_ok=True, parents=True)

    try:
        plan_info = _build_plan(prompt, resolved_files)
        if plan_info["operation"] == "concat":
            _ffmpeg_concat(output_path, resolved_files)
            summary = f"Merged {len(resolved_files)} source item(s) into {output_path.name} following the EDL plan."
        elif plan_info["operation"] == "trim":
            input_path = resolved_files[0]
            _ffmpeg_trim(output_path, input_path)
            summary = f"Trimmed {input_path.name} into {output_path.name} using the planned EDL cut."
        elif plan_info["operation"] == "transcribe":
            transcript = _transcribe_video(resolved_files[0])
            transcript_path = output_path.with_suffix(".txt")
            with transcript_path.open("x", encoding="utf-8") as transcript_file:
                transcript_file.write(transcript)
            _remember_video_edit(
                f"Transcribed {resolved_files[0].name} into {transcript_path.name}.",
                str(transcript_path),
                prompt,
                [str(path) for path in resolved_files],
            )
            return {
                "success": True,
                "operation": "transcribe",
                "output_path": str(transcript_path),
                "transcript": transcript,
                "tooling": status,
            }
        else:
            input_path = resolved_files[0]
            subprocess.run(["ffmpeg", "-n", "-i", str(input_path), "-c", "copy", str(output_path)], check=True, capture_output=True, text=True)
            summary = f"Passed through {input_path.name} to {output_path.name} as the default EDL fallback."

        _remember_video_edit(summary, str(output_path), prompt, [str(path) for path in resolved_files])
        return {
            "success": True,
            "operation": plan_info["operation"],
            "output_path": str(output_path),
            "tooling": status,
        }
    except subprocess.CalledProcessError as exc:
        return {
            "success": False,
            "error": "FFmpeg failed while creating the edited video.",
            "stderr": exc.stderr,
            "stdout": exc.stdout,
            "tooling": status,
        }
    except RuntimeError as exc:
        return {"success": False, "error": str(exc), "tooling": status}


def run_video_edit(
    prompt: str,
    source_files: list | None = None,
    output_filename: str = "edited_video.mp4",
    trim_start: float | None = None,
    trim_end: float | None = None,
) -> dict:
    if not prompt or not str(prompt).strip():
        return {"success": False, "error": "A prompt is required for video editing."}

    project_id = "manual_video_edit"
    plan = build_edit_plan(project_id, prompt, source_files, output_filename, trim_start, trim_end)
    return execute_edit_plan(plan)
