from __future__ import annotations

import logging
import os
import queue
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

LOGGER = logging.getLogger("hsx.hardware.speaker")
MAX_SPEECH_CHARS = 1000


class Speaker:
    """Single-worker Piper-to-ALSA speech queue with bounded memory."""

    def __init__(
        self,
        *,
        model_path: str | Path | None = None,
        device: str | None = None,
        queue_size: int = 8,
    ):
        self.model_path = Path(model_path or os.getenv("HSX_PIPER_MODEL", "")).expanduser()
        requested_device = device if device is not None else os.getenv("HSX_AUDIO_DEVICE", "default")
        self.device = "default" if requested_device in {"", "alsa/default"} else requested_device
        if queue_size < 1:
            raise ValueError("queue_size must be positive.")
        self._queue: queue.Queue[str | None] = queue.Queue(maxsize=queue_size)
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._closed = False

    @staticmethod
    def enabled() -> bool:
        return os.getenv("HSX_ENABLE_VOICE_ALERTS", "").strip().lower() in {
            "1", "true", "yes", "on",
        }

    def start(self) -> bool:
        with self._lock:
            if self._closed:
                raise RuntimeError("The speaker queue has been stopped.")
            if self._thread is not None and self._thread.is_alive():
                return False
            if not self.enabled():
                return False
            if not str(self.model_path) or not self.model_path.is_file():
                LOGGER.warning("Voice alerts disabled: HSX_PIPER_MODEL is missing or unreadable.")
                return False
            if shutil.which("piper") is None or shutil.which("aplay") is None:
                LOGGER.warning("Voice alerts disabled: install piper-tts and alsa-utils.")
                return False
            self._thread = threading.Thread(
                target=self._worker,
                name="hsx-speaker",
                daemon=True,
            )
            self._thread.start()
            return True

    def speak_async(self, text: str) -> bool:
        if not isinstance(text, str) or not text.strip():
            return False
        if not self.enabled():
            return False
        with self._lock:
            if self._closed:
                return False
            self.start()
            if self._thread is None or not self._thread.is_alive():
                return False
            try:
                self._queue.put_nowait(text.strip()[:MAX_SPEECH_CHARS])
            except queue.Full:
                LOGGER.warning("Voice alert queue is full; dropping the oldest queued announcement.")
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                    self._queue.put_nowait(text.strip()[:MAX_SPEECH_CHARS])
                except (queue.Empty, queue.Full):
                    return False
            return True

    def stop(self, timeout: float = 3.0) -> None:
        with self._lock:
            self._closed = True
            thread = self._thread
        if thread is not None and thread.is_alive():
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except queue.Empty:
                    pass
                self._queue.put_nowait(None)
            thread.join(timeout)

    def _worker(self) -> None:
        while True:
            text = self._queue.get()
            try:
                if text is None:
                    return
                self._speak(text)
            except (OSError, RuntimeError, subprocess.SubprocessError):
                LOGGER.exception("Could not produce a local voice alert.")
            finally:
                self._queue.task_done()

    def _speak(self, text: str) -> None:
        piper = shutil.which("piper")
        aplay = shutil.which("aplay")
        if piper is None or aplay is None:
            raise RuntimeError("piper and aplay are required for local voice alerts.")
        descriptor, filename = tempfile.mkstemp(prefix="hsx-voice-", suffix=".wav")
        os.close(descriptor)
        audio_path = Path(filename)
        try:
            try:
                subprocess.run(
                    [piper, "--model", str(self.model_path), "--output_file", str(audio_path)],
                    input=text + "\n",
                    text=True,
                    stdin=None,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=45,
                    check=True,
                )
                command = [aplay, "-q"]
                if self.device and self.device != "default":
                    command.extend(["-D", self.device])
                command.append(str(audio_path))
                subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=20,
                    check=True,
                )
            except subprocess.TimeoutExpired as error:
                raise RuntimeError("Local voice alert exceeded its time limit.") from error
        finally:
            audio_path.unlink(missing_ok=True)


SPEAKER = Speaker()
