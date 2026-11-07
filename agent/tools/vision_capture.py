from __future__ import annotations

import logging
import os
from pathlib import Path

from agent.hardware.camera import CAMERA, MAX_FRAME_AGE_SECONDS
from config import DATA_DIR

LOGGER = logging.getLogger("hsx.tools.vision_capture")
MAX_JPEG_BYTES = 2 * 1024 * 1024


def enabled() -> bool:
    return os.getenv("HSX_ENABLE_VISION_CAPTURE", "0").strip().casefold() in {"1", "true", "yes", "on"}


def disabled_reason() -> str:
    if not enabled():
        return "Agent camera snapshots are disabled. Set HSX_ENABLE_VISION_CAPTURE=1 in .env to enable them."
    status = CAMERA.health_check()
    if not status.get("device_available"):
        return f"Camera device {CAMERA.device} is unavailable. Connect it or set HSX_CAMERA_DEVICE to the correct device."
    if not shutil_ffmpeg_available():
        return "Camera snapshots need ffmpeg. Install ffmpeg in the host environment."
    return "Camera snapshot is unavailable; check the host camera service logs."


def shutil_ffmpeg_available() -> bool:
    import shutil

    return shutil.which("ffmpeg") is not None


def _store_frame(data: bytes, destination: Path) -> Path:
    if len(data) < 4 or len(data) > MAX_JPEG_BYTES or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise RuntimeError("Camera service returned an invalid or oversized JPEG frame.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(destination.parent, 0o700)
    except OSError:
        pass
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        temporary.write_bytes(data)
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def camera_snapshot(*, output_path: Path | None = None, timeout_seconds: float = 15.0) -> Path:
    if timeout_seconds <= 0 or timeout_seconds > 60:
        raise ValueError("Camera capture timeout must be between 0 and 60 seconds.")
    if not CAMERA.is_started() and not enabled():
        raise RuntimeError(disabled_reason())
    owned_start = not CAMERA.is_started()
    owner = f"snapshot-{os.getpid()}-{id(object())}"
    destination = output_path or DATA_DIR / "snapshots" / "latest.jpg"
    try:
        before = CAMERA.latest_frame()
        after_version = before.version if before else -1
        if owned_start:
            CAMERA.acquire(owner)
        frame = CAMERA.wait_for_frame(after_version, timeout=min(timeout_seconds, MAX_FRAME_AGE_SECONDS + 2))
        if frame is None:
            detail = CAMERA.health_check().get("last_error") or disabled_reason()
            raise RuntimeError(str(detail))
        return _store_frame(frame.data, Path(destination))
    except (OSError, RuntimeError, ValueError):
        LOGGER.exception("Camera service snapshot failed.")
        raise
    finally:
        if owned_start:
            CAMERA.release(owner)


def capture_frame(*, device: str | None = None, output_path: Path | None = None, timeout_seconds: float = 15.0) -> Path:
    if device and Path(device) != Path(CAMERA.device):
        raise ValueError("CameraService owns the configured camera device; change HSX_CAMERA_DEVICE to select another device.")
    return camera_snapshot(output_path=output_path, timeout_seconds=timeout_seconds)


def run() -> dict:
    path = camera_snapshot()
    return {"success": True, "path": str(path), "bytes": path.stat().st_size, "message": "A single camera frame was captured from CameraService."}
