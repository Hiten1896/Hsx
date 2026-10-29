import shutil
import subprocess
from pathlib import Path, PureWindowsPath

from agent.workspace import Workspace
from agent.workspace import task_output_path
from config import WORKSPACE_DIR, WORK_DIR


WORKSPACE = Workspace(WORKSPACE_DIR, WORK_DIR)


def run(
    mode: str,
    duration_seconds: int = 10,
    filename: str = "",
    target_device: str = "local_host",
) -> dict:
    if mode not in {"snapshot", "video"}:
        raise ValueError("mode must be snapshot or video.")
    duration_seconds = min(max(int(duration_seconds), 1), 60)
    extension = ".jpg" if mode == "snapshot" else ".mp4"
    requested_name = PureWindowsPath(filename).name if filename else f"camera_{mode}{extension}"
    suffix = Path(requested_name).suffix.lower()
    if mode == "snapshot" and suffix not in {".jpg", ".jpeg"}:
        raise ValueError("Snapshot output must use a .jpg or .jpeg filename.")
    if mode == "video" and suffix not in ({".mp4", ".webm"} if target_device == "client_browser" else {".mp4"}):
        raise ValueError("Video output must use .mp4 (host) or .mp4/.webm (browser) format.")

    if target_device == "client_browser":
        return {
            "success": True,
            "browser_action": {
                "type": "camera_record",
                "mode": mode,
                "duration_seconds": duration_seconds,
                "filename": requested_name,
            },
            "message": "A camera capture request is ready for explicit confirmation in the requesting browser.",
        }
    if target_device != "local_host":
        raise ValueError("target_device must be local_host or client_browser.")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required for host camera capture. Install it with: sudo apt install ffmpeg")
    output = task_output_path(WORKSPACE, requested_name)
    if mode == "snapshot":
        command = [ffmpeg, "-nostdin", "-v", "error", "-y", "-f", "v4l2", "-i", "/dev/video0", "-frames:v", "1", str(output)]
    else:
        command = [
            ffmpeg, "-nostdin", "-v", "error", "-y", "-f", "v4l2", "-i", "/dev/video0",
            "-t", str(duration_seconds), "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(output),
        ]
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
        try:
            _, stderr = process.communicate(timeout=duration_seconds + 20)
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.communicate()
            raise RuntimeError(
                f"Camera capture timed out after {duration_seconds + 20} seconds."
            ) from error
    if process.returncode:
        raise RuntimeError(stderr[-2000:] or f"ffmpeg exited with {process.returncode}.")
    return {
        "success": True,
        "target_device": "local_host",
        "path": output.relative_to(WORKSPACE.work_dir).as_posix(),
        "bytes": output.stat().st_size,
    }
