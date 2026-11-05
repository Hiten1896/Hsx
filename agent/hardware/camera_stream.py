from __future__ import annotations

import ipaddress
import logging
import os
import shutil
import subprocess
import threading
from urllib.parse import urlsplit

LOGGER = logging.getLogger("hsx.hardware.camera_stream")
LOCAL_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "fc00::/7",
        "fe80::/10",
        "::1/128",
    )
)


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _validate_rtsp_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "rtsp"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("RTSP target must be an rtsp:// URL without credentials or query values.")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        host = parsed.hostname.lower()
        if host not in {"localhost"} and not host.endswith(".local"):
            raise ValueError("RTSP target must use a private IP, localhost, or a .local hostname.")
    else:
        if not any(address in network for network in LOCAL_NETWORKS):
            raise ValueError("RTSP target must resolve to a local/private network address.")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("RTSP target port is invalid.")
    return value


class CameraStream:
    """Supervise one ffmpeg publisher to an already-running local RTSP server."""

    def __init__(
        self,
        *,
        device: str | None = None,
        rtsp_url: str | None = None,
        restart_min_seconds: float = 5.0,
        restart_max_seconds: float = 60.0,
        check_interval: float = 2.0,
    ):
        self.device = device or os.getenv("HSX_CAMERA_DEVICE", "/dev/video0")
        self.rtsp_url = rtsp_url if rtsp_url is not None else os.getenv("HSX_RTSP_URL", "")
        self.restart_min_seconds = restart_min_seconds
        self.restart_max_seconds = restart_max_seconds
        self.check_interval = check_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._lock = threading.RLock()
        self._last_error = ""
        if restart_min_seconds <= 0 or restart_max_seconds < restart_min_seconds:
            raise ValueError("Camera stream restart bounds are invalid.")
        if check_interval <= 0:
            raise ValueError("Camera stream check interval must be positive.")
        if self.rtsp_url:
            self.rtsp_url = _validate_rtsp_url(self.rtsp_url)

    @staticmethod
    def enabled() -> bool:
        return _enabled(os.getenv("HSX_ENABLE_CAMERA_STREAM"))

    def command(self, ffmpeg: str) -> list[str]:
        if not self.rtsp_url:
            raise ValueError("Set HSX_RTSP_URL to a local RTSP server before starting the stream.")
        return [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "v4l2",
            "-video_size",
            "640x480",
            "-framerate",
            "15",
            "-i",
            self.device,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-b:v",
            "400k",
            "-maxrate",
            "400k",
            "-bufsize",
            "800k",
            "-pix_fmt",
            "yuv420p",
            "-f",
            "rtsp",
            "-rtsp_transport",
            "tcp",
            self.rtsp_url,
        ]

    def health_check(self) -> dict:
        with self._lock:
            process = self._process
            return {
                "running": process is not None and process.poll() is None,
                "device_available": os.path.exists(self.device),
                "pid": process.pid if process is not None and process.poll() is None else None,
                "last_error": self._last_error,
            }

    def start(self) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            if not self.rtsp_url:
                raise ValueError("Set HSX_RTSP_URL to a local RTSP server before starting the stream.")
            if _validate_rtsp_url(self.rtsp_url) != self.rtsp_url:
                raise ValueError("Invalid RTSP target.")
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._supervise,
                name="hsx-camera-stream",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
        if self._thread is not None:
            self._thread.join(timeout)
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=timeout)

    def _supervise(self) -> None:
        delay = self.restart_min_seconds
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            self._last_error = "ffmpeg is not installed."
            LOGGER.error("Camera RTSP stream is disabled: install ffmpeg.")
            return
        while not self._stop.is_set():
            if not os.path.exists(self.device):
                self._last_error = f"Camera device is unavailable: {self.device}"
                LOGGER.warning("%s", self._last_error)
                self._stop.wait(delay)
                delay = min(self.restart_max_seconds, delay * 2)
                continue
            try:
                process = subprocess.Popen(
                    self.command(ffmpeg),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                )
                with self._lock:
                    self._process = process
                while not self._stop.wait(self.check_interval):
                    if process.poll() is not None or not os.path.exists(self.device):
                        break
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                if not self._stop.is_set():
                    self._last_error = "Camera or RTSP publisher exited; retrying."
                    LOGGER.warning("%s", self._last_error)
            except (OSError, RuntimeError, ValueError):
                self._last_error = "Could not start the ffmpeg RTSP publisher."
                LOGGER.exception("%s", self._last_error)
            finally:
                with self._lock:
                    self._process = None
            if not self._stop.is_set():
                self._stop.wait(delay)
                delay = min(self.restart_max_seconds, delay * 2)
            else:
                delay = self.restart_min_seconds


CAMERA_STREAM = CameraStream()
