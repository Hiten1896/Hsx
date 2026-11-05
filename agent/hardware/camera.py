from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass

LOGGER = logging.getLogger("hsx.hardware.camera")
FRAME_RATE = 15
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
MAX_FRAME_BYTES = 2 * 1024 * 1024
MAX_FRAME_AGE_SECONDS = 3.0
JPEG_START = b"\xff\xd8"
JPEG_END = b"\xff\xd9"


@dataclass(frozen=True)
class CameraFrame:
    version: int
    data: bytes
    captured_at: float


class CameraService:
    """Keep one bounded FFmpeg capture running and share its latest JPEG frame."""

    def __init__(
        self,
        *,
        device: str | None = None,
        restart_min_seconds: float = 2.0,
        restart_max_seconds: float = 30.0,
        frame_rate: int = FRAME_RATE,
        width: int = FRAME_WIDTH,
        height: int = FRAME_HEIGHT,
    ):
        self.device = device or os.getenv("HSX_CAMERA_DEVICE", "/dev/video0")
        if not self.device.startswith("/dev/") or "\n" in self.device or "\r" in self.device:
            raise ValueError("Camera device must be a /dev path.")
        if restart_min_seconds <= 0 or restart_max_seconds < restart_min_seconds:
            raise ValueError("Camera restart bounds are invalid.")
        if (
            frame_rate < 1
            or frame_rate > 30
            or width < 1
            or width > 1280
            or height < 1
            or height > 720
        ):
            raise ValueError("Camera frame rate or resolution is invalid.")
        self.restart_min_seconds = restart_min_seconds
        self.restart_max_seconds = restart_max_seconds
        self.frame_rate = frame_rate
        self.width = width
        self.height = height
        self._stop = threading.Event()
        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._frame: CameraFrame | None = None
        self._last_error = ""

    def command(self, ffmpeg: str) -> list[str]:
        return [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-threads",
            "1",
            "-f",
            "v4l2",
            "-framerate",
            str(self.frame_rate),
            "-video_size",
            f"{self.width}x{self.height}",
            "-i",
            self.device,
            "-an",
            "-vf",
            f"scale={self.width}:{self.height}",
            "-c:v",
            "mjpeg",
            "-q:v",
            "8",
            "-f",
            "image2pipe",
            "pipe:1",
        ]

    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._supervise,
            name="hsx-camera",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        with self._condition:
            process = self._process
            self._condition.notify_all()
        if process is not None and process.poll() is None:
            process.terminate()
        if self._thread is not None:
            self._thread.join(timeout)
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=timeout)

    def latest_frame(self) -> CameraFrame | None:
        with self._condition:
            frame = self._frame
        if frame is None or time.monotonic() - frame.captured_at > MAX_FRAME_AGE_SECONDS:
            return None
        return frame

    def is_started(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wait_for_frame(
        self,
        after_version: int,
        timeout: float = MAX_FRAME_AGE_SECONDS,
    ) -> CameraFrame | None:
        if timeout <= 0:
            raise ValueError("Frame wait timeout must be positive.")

        def available() -> bool:
            frame = self._frame
            return self._stop.is_set() or (
                frame is not None
                and frame.version > after_version
                and time.monotonic() - frame.captured_at <= MAX_FRAME_AGE_SECONDS
            )

        with self._condition:
            self._condition.wait_for(available, timeout=timeout)
            frame = self._frame
        if (
            frame is None
            or frame.version <= after_version
            or time.monotonic() - frame.captured_at > MAX_FRAME_AGE_SECONDS
        ):
            return None
        return frame

    def health_check(self) -> dict[str, object]:
        with self._condition:
            process = self._process
            frame = self._frame
        return {
            "running": process is not None and process.poll() is None,
            "device_available": os.path.exists(self.device),
            "frame_age_seconds": (
                round(time.monotonic() - frame.captured_at, 3) if frame else None
            ),
            "last_error": self._last_error,
        }

    def _publish_frame(self, data: bytes) -> None:
        if len(data) < 4 or len(data) > MAX_FRAME_BYTES:
            return
        if not data.startswith(JPEG_START) or not data.endswith(JPEG_END):
            return
        with self._condition:
            previous_version = self._frame.version if self._frame else 0
            self._frame = CameraFrame(previous_version + 1, data, time.monotonic())
            self._last_error = ""
            self._condition.notify_all()

    def _consume_mjpeg(self, stream) -> None:
        pending = bytearray()
        while not self._stop.is_set():
            chunk = stream.read(4096)
            if not chunk:
                return
            pending.extend(chunk)
            while True:
                start = pending.find(JPEG_START)
                if start < 0:
                    if len(pending) > 1:
                        del pending[:-1]
                    break
                if start:
                    del pending[:start]
                end = pending.find(JPEG_END, len(JPEG_START))
                if end < 0:
                    if len(pending) > MAX_FRAME_BYTES:
                        next_start = pending.find(JPEG_START, len(JPEG_START))
                        if next_start < 0:
                            pending.clear()
                        else:
                            del pending[:next_start]
                    break
                frame_end = end + len(JPEG_END)
                self._publish_frame(bytes(pending[:frame_end]))
                del pending[:frame_end]

    def _supervise(self) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            self._last_error = "ffmpeg is not installed."
            LOGGER.error("Live camera is unavailable: install ffmpeg.")
            return
        delay = self.restart_min_seconds
        while not self._stop.is_set():
            if not os.path.exists(self.device):
                self._last_error = f"Camera device is unavailable: {self.device}"
                self._stop.wait(delay)
                delay = min(self.restart_max_seconds, delay * 2)
                continue
            process = None
            try:
                process = subprocess.Popen(
                    self.command(ffmpeg),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                )
                with self._condition:
                    self._process = process
                    self._condition.notify_all()
                if process.stdout is None:
                    raise RuntimeError("FFmpeg camera output pipe was not created.")
                self._consume_mjpeg(process.stdout)
                if not self._stop.is_set():
                    self._last_error = "FFmpeg camera capture stopped; retrying."
                    LOGGER.warning("%s", self._last_error)
            except (OSError, RuntimeError, ValueError):
                self._last_error = "Could not start the FFmpeg camera capture."
                LOGGER.exception("%s", self._last_error)
            finally:
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                with self._condition:
                    self._process = None
                    self._condition.notify_all()
                if process is not None and process.stdout is not None:
                    process.stdout.close()
            if not self._stop.is_set():
                self._stop.wait(delay)
                delay = min(self.restart_max_seconds, delay * 2)


CAMERA = CameraService()
