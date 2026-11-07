from __future__ import annotations

import json
import heapq
import io
import logging
import os
import re
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Callable

from agent.ml.storage import atomic_write_json
from config import DATA_DIR

LOGGER = logging.getLogger("hsx.hardware.motion_monitor")
MAX_EVENTS = 500


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


class MotionMonitor:
    """Detect frame changes from the shared CameraService without opening a device."""

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
        on_event: Callable[[dict], None] | None = None,
    ):
        self.device = device or os.getenv("HSX_CAMERA_DEVICE", "/dev/video0")
        self.snapshot_dir = snapshot_dir or DATA_DIR / "snapshots" / "motion"
        self.events_path = events_path or DATA_DIR / "motion_events.json"
        self.config_path = config_path or DATA_DIR / "motion.conf"
        self.poll_seconds = poll_seconds
        self.restart_seconds = restart_seconds
        self.max_snapshots = max_snapshots
        self.on_event = on_event
        if not re.fullmatch(r"/dev/video\d+", self.device):
            raise ValueError("Motion camera device must be a /dev/videoN path.")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._camera_owned = False
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
        from agent.hardware.camera import CAMERA

        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            camera = CAMERA.health_check()
            return {
                "running": running,
                "device_available": camera.get("device_available", False),
                "pid": CAMERA.health_check().get("pid"),
                "last_error": self._last_error or camera.get("last_error", ""),
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
        if self._camera_owned:
            from agent.hardware.camera import CAMERA

            CAMERA.release("motion-monitor", timeout=timeout)
            self._camera_owned = False

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
        new_events = []
        for path in new_paths:
            try:
                timestamp = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
            except OSError:
                continue
            event = {
                "timestamp": timestamp,
                "event": "motion_snapshot",
                "snapshot": path.name,
            }
            existing.append(event)
            new_events.append(event)
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
            return
        if self.on_event is not None:
            for event in new_events:
                try:
                    self.on_event(event)
                except Exception:
                    LOGGER.exception("Could not publish Motion event %s.", event["snapshot"])

    def _supervise(self) -> None:
        try:
            from PIL import Image, ImageChops, ImageStat
        except ImportError:
            self._last_error = "Frame motion needs Pillow. Use the approved Pillow setup recipe."
            return

        from agent.hardware.camera import CAMERA

        self._camera_owned = True
        CAMERA.acquire("motion-monitor")
        previous = None
        last_version = -1
        self._last_error = ""
        while not self._stop.is_set():
            frame = CAMERA.latest_frame()
            if frame and frame.version != last_version:
                last_version = frame.version
                try:
                    current = Image.open(io.BytesIO(frame.data)).convert("L").resize((80, 60))
                    if previous is not None:
                        difference = ImageStat.Stat(ImageChops.difference(previous, current)).mean[0]
                        if difference >= 18:
                            self.snapshot_dir.mkdir(parents=True, exist_ok=True)
                            destination = self.snapshot_dir / f"motion-{frame.version:012d}.jpg"
                            destination.write_bytes(frame.data)
                            self._record_new_snapshots()
                    previous = current
                except (OSError, ValueError):
                    LOGGER.exception("Camera frame motion analysis failed.")
            status = CAMERA.health_check()
            if not status.get("running"):
                self._last_error = str(status.get("last_error") or "CameraService is not running.")
            self._stop.wait(self.poll_seconds)
        if self._camera_owned:
            CAMERA.release("motion-monitor")
            self._camera_owned = False


MOTION_MONITOR = MotionMonitor()
