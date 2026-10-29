import shutil
import subprocess
import threading
import uuid
from pathlib import Path

from agent.workspace import Workspace, task_output_path
from config import WORKSPACE_DIR, WORK_DIR

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()
WORKSPACE = Workspace(WORKSPACE_DIR, WORK_DIR)


def _download(job_id: str, url: str, filename: str, max_duration: int, max_size_mb: int) -> None:
    destination = Path(_JOBS[job_id]["destination"])
    try:
        command = [
            "yt-dlp", "--no-playlist", "--max-filesize", f"{max_size_mb}M",
            "--match-filter", f"duration <= {max_duration}",
            "-o", str(destination), url,
        ]
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=300)
        with _LOCK:
            _JOBS[job_id] = {
                "job_id": job_id,
                "status": "done" if result.returncode == 0 else "failed",
                "output": str(destination) if result.returncode == 0 else "",
                "error": result.stderr[-1000:],
            }
    except (OSError, subprocess.TimeoutExpired) as error:
        with _LOCK:
            _JOBS[job_id] = {"job_id": job_id, "status": "failed", "error": str(error)}
    if destination.exists() and destination.stat().st_size > max_size_mb * 1024 * 1024:
        destination.unlink()
        with _LOCK:
            _JOBS[job_id] = {"job_id": job_id, "status": "failed", "error": "Downloaded file exceeded the size limit."}


def run(url: str, filename: str = "", max_duration_seconds: int = 3600, max_size_mb: int = 512) -> dict:
    if not url.startswith(("https://", "http://")):
        return {"success": False, "error": "Only HTTP(S) URLs are allowed."}
    filename = Path(filename or f"download_{uuid.uuid4().hex}.media").name
    if filename in {".", ".."}:
        return {"success": False, "error": "Invalid output filename."}
    max_duration_seconds = max(1, min(max_duration_seconds, 86400))
    max_size_mb = max(1, min(max_size_mb, 2048))
    if shutil.which("yt-dlp") is None:
        return {"success": False, "error": "yt-dlp is not installed."}
    job_id = uuid.uuid4().hex
    destination = task_output_path(WORKSPACE, filename)
    with _LOCK:
        _JOBS[job_id] = {"job_id": job_id, "status": "queued", "destination": str(destination)}
    threading.Thread(
        target=_download,
        args=(job_id, url, filename, max_duration_seconds, max_size_mb),
        daemon=True,
    ).start()
    return {"success": True, "job_id": job_id, "status": "queued", "output_directory": str(destination.parent)}


def get_job(job_id: str) -> dict | None:
    with _LOCK:
        return _JOBS.get(job_id)
