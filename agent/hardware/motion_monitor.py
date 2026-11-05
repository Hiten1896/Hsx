from __future__ import annotations

import json
import heapq
import logging
import os
import re
import shutil
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

from agent.ml.storage import atomic_write_json
from config import DATA_DIR

LOGGER = logging.getLogger("hsx.hardware.motion_monitor")
MAX_EVENTS = 500


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


class MotionMonitor:
    """Supervise headless Motion and index only its local snapshots."""

    def __init__(
        self,
        *,
        device: str | None = None,
        snapshot_dir: Path | None = None,
        events_path: Path | None = None,
        config_path: Path | None = None,
        poll_seconds: float = 3.0,
        restart_seconds: float = 10.0,
        max_snapshots: int = 500,
    ):
        self.device = device or os.getenv("HSX_CAMERA_DEVICE", "/dev/video0")
        self.snapshot_dir = snapshot_dir or DATA_DIR / "snapshots" / "motion"
        self.events_path = events_path or DATA_DIR / "motion_events.json"
        self.config_path = config_path or DATA_DIR / "motion.conf"
        self.poll_seconds = poll_seconds
        self.restart_seconds = restart_seconds
        self.max_snapshots = max_snapshots
        if not re.fullmatch(r"/dev/video\d+", self.device):
            raise ValueError("Motion camera device must be a /dev/videoN path.")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._lock = threading.RLock()
        self._seen: set[str] = set()
        self._last_error = ""
        if poll_seconds <= 0 or restart_seconds <= 0:
            raise ValueError("Motion monitor intervals must be positive.")
        if max_snapshots < 10 or max_snapshots > 10_000:
            raise ValueError("max_snapshots must be between 10 and 10,000.")

    @staticmethod
    def enabled() -> bool:
        return _enabled(os.getenv("HSX_ENABLE_MOTION_MONITOR"))

    def _write_config(self) -> None:
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.snapshot_dir, 0o700)
        settings = (
            "daemon off\n"
            "setup_mode off\n"
            f"videodevice {self.device}\n"
            "width 640\n"
            "height 480\n"
            "framerate 5\n"
            "threshold 1500\n"
            "event_gap 10\n"
            "picture_output best\n"
            "picture_quality 60\n"
            "picture_filename %Y%m%d-%H%M%S-%q\n"
            "movie_output off\n"
            "stream_port 0\n"
            "webcontrol_port 0\n"
            "target_dir " + str(self.snapshot_dir.resolve()) + "\n"
            "log_level 6\n"
        )
        temporary = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(settings, encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.config_path)

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
            self._write_config()
            existing: list[tuple[int, str]] = []
            for path in self.snapshot_dir.glob("*.jpg"):
                try:
                    existing.append((path.stat().st_mtime_ns, path.name))
                except OSError:
                    continue
            self._seen = {name for _, name in heapq.nlargest(MAX_EVENTS, existing)}
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._supervise,
                name="hsx-motion-monitor",
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

    def _record_new_snapshots(self) -> None:
        recent_with_times: list[tuple[int, str, Path]] = []
        for path in self.snapshot_dir.glob("*.jpg"):
            try:
                modified = path.stat().st_mtime_ns
            except OSError:
                continue
            item = (modified, path.name, path)
            if len(recent_with_times) < self.max_snapshots:
                heapq.heappush(recent_with_times, item)
            elif modified > recent_with_times[0][0]:
                _, _, expired = heapq.heapreplace(recent_with_times, item)
                try:
                    expired.unlink()
                except OSError:
                    LOGGER.warning("Could not prune old Motion snapshot %s.", expired)
            else:
                try:
                    path.unlink()
                except OSError:
                    LOGGER.warning("Could not prune old Motion snapshot %s.", path)
        recent = [
            item[2]
            for item in sorted(recent_with_times, key=lambda value: value[0])
        ]
        recent_names = {path.name for path in recent}
        self._seen.intersection_update(recent_names)
        new_paths = [path for path in recent if path.name not in self._seen]
        if not new_paths:
            return
        try:
            existing = json.loads(self.events_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            existing = []
        except (OSError, json.JSONDecodeError):
            LOGGER.exception("Could not read the Motion event log.")
            return
        if not isinstance(existing, list):
            LOGGER.error("Motion event log has an invalid JSON shape; refusing to overwrite it.")
            return
        for path in new_paths:
            try:
                timestamp = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
            except OSError:
                continue
            existing.append({
                "timestamp": timestamp,
                "event": "motion_snapshot",
                "snapshot": path.name,
            })
            try:
                os.chmod(path, 0o600)
            except OSError:
                LOGGER.warning("Could not restrict permissions on Motion snapshot %s.", path)
            self._seen.add(path.name)
        try:
            atomic_write_json(self.events_path, existing[-MAX_EVENTS:])
            os.chmod(self.events_path, 0o600)
        except (OSError, TypeError, ValueError):
            LOGGER.exception("Could not persist Motion snapshot events.")

    def _supervise(self) -> None:
        binary = shutil.which("motion")
        if binary is None:
            self._last_error = "motion is not installed."
            LOGGER.error("Motion monitor is disabled: install motion.")
            return
        while not self._stop.is_set():
            self._record_new_snapshots()
            if not os.path.exists(self.device):
                self._last_error = f"Camera device is unavailable: {self.device}"
                LOGGER.warning("%s", self._last_error)
                self._stop.wait(self.restart_seconds)
                continue
            try:
                process = subprocess.Popen(
                    [binary, "-n", "-c", str(self.config_path)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                )
                with self._lock:
                    self._process = process
                while not self._stop.wait(self.poll_seconds):
                    self._record_new_snapshots()
                    if process.poll() is not None:
                        self._last_error = f"motion exited with status {process.returncode}."
                        LOGGER.warning("%s", self._last_error)
                        break
                    if not os.path.exists(self.device):
                        self._last_error = f"Camera device disconnected: {self.device}"
                        LOGGER.warning("%s", self._last_error)
                        process.terminate()
                        break
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            except OSError:
                self._last_error = "Could not start the Motion monitor."
                LOGGER.exception("%s", self._last_error)
            finally:
                with self._lock:
                    self._process = None
            self._record_new_snapshots()
            if not self._stop.is_set():
                self._stop.wait(self.restart_seconds)


MOTION_MONITOR = MotionMonitor()
