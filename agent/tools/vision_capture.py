from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from config import DATA_DIR

LOGGER = logging.getLogger("hsx.tools.vision_capture")
MAX_JPEG_BYTES = 2 * 1024 * 1024


def enabled() -> bool:
    return os.getenv("HSX_ENABLE_VISION_CAPTURE", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def capture_frame(
    *,
    device: str | None = None,
    output_path: Path | None = None,
    timeout_seconds: float = 15.0,
) -> Path:
    """Capture one bounded JPEG and atomically replace the local latest-frame file."""
    if not enabled():
        raise RuntimeError("Host camera capture is disabled by HSX_ENABLE_VISION_CAPTURE.")
    if timeout_seconds <= 0 or timeout_seconds > 60:
        raise ValueError("Camera capture timeout must be between 0 and 60 seconds.")
    camera_device = device or os.getenv("HSX_CAMERA_DEVICE", "/dev/video0")
    destination = output_path or DATA_DIR / "snapshots" / "latest.jpg"
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(destination.parent, 0o700)
    ffmpeg = shutil.which("ffmpeg")
    fswebcam = shutil.which("fswebcam")
    if ffmpeg is None and fswebcam is None:
        raise RuntimeError("Camera capture requires ffmpeg or fswebcam.")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".latest-",
        suffix=".jpg",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        if ffmpeg:
            command = [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "v4l2",
                "-video_size",
                "640x480",
                "-i",
                camera_device,
                "-frames:v",
                "1",
                "-vf",
                "scale=640:480",
                "-q:v",
                "8",
                str(temporary),
            ]
        else:
            command = [
                fswebcam,
                "--device",
                camera_device,
                "--resolution",
                "640x480",
                "--jpeg",
                "70",
                "--no-banner",
                "--save",
                str(temporary),
            ]
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(f"Camera capture timed out after {timeout_seconds:g} seconds.") from error
        if result.returncode:
            raise RuntimeError(f"Camera capture utility exited with status {result.returncode}.")
        size = temporary.stat().st_size
        if size < 4 or size > MAX_JPEG_BYTES:
            raise RuntimeError("Camera frame is empty or exceeds the 2 MiB size limit.")
        with temporary.open("rb") as source:
            if source.read(2) != b"\xff\xd8":
                raise RuntimeError("Camera capture utility did not produce a JPEG frame.")
            source.seek(-2, os.SEEK_END)
            if source.read(2) != b"\xff\xd9":
                raise RuntimeError("Captured JPEG frame is incomplete.")
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
        return destination
    except (OSError, RuntimeError, ValueError):
        LOGGER.exception("Local vision frame capture failed.")
        raise
    finally:
        temporary.unlink(missing_ok=True)


def run() -> dict:
    path = capture_frame()
    return {
        "success": True,
        "path": str(path),
        "bytes": path.stat().st_size,
        "message": "A single local camera frame was captured.",
    }
