from __future__ import annotations

import io
import logging
import math
import os
import queue
import shutil
import struct
import subprocess
import threading
import time
import wave

from agent.hardware.speaker import SPEAKER

LOGGER = logging.getLogger("hsx.hardware.audio_alerts")
SAMPLE_RATE = 16_000
TONE_SECONDS = 0.12
MAX_ALERTS = 8
WARNING_COOLDOWN_SECONDS = 60.0
FREQUENCIES = {"success": (880,), "warning": (440, 330)}


class AudioAlerts:
    """Generate small WAV tones in memory and play them asynchronously via ALSA."""

    def __init__(self, *, device: str | None = None):
        requested_device = device if device is not None else os.getenv("HSX_AUDIO_DEVICE", "default")
        self.device = "default" if requested_device in {"", "alsa/default"} else requested_device
        self._queue: queue.Queue[str | None] = queue.Queue(maxsize=MAX_ALERTS)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_warning = -WARNING_COOLDOWN_SECONDS
        self._closed = False

    @staticmethod
    def enabled() -> bool:
        return os.getenv("HSX_ENABLE_AUDIO_ALERTS", "").strip().lower() in {
            "1", "true", "yes", "on",
        }

    def play_async(self, kind: str) -> bool:
        if kind not in FREQUENCIES:
            raise ValueError("kind must be success or warning.")
        if not self.enabled():
            return False
        with self._lock:
            if self._closed:
                return False
            if kind == "warning":
                now = time.monotonic()
                if now - self._last_warning < WARNING_COOLDOWN_SECONDS:
                    return False
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._worker,
                    name="hsx-audio-alerts",
                    daemon=True,
                )
                self._thread.start()
            try:
                self._queue.put_nowait(kind)
            except queue.Full:
                LOGGER.warning("Audio alert queue is full; dropping alert %s.", kind)
                return False
            if kind == "warning":
                self._last_warning = now
            return True

    def notify_success(self, message: str = "Hsx task completed successfully.") -> bool:
        if SPEAKER.enabled() and SPEAKER.speak_async(message):
            return True
        return self.play_async("success")

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
            kind = self._queue.get()
            try:
                if kind is None:
                    return
                self._play(kind)
            except (OSError, RuntimeError, subprocess.SubprocessError):
                LOGGER.exception("Could not play local audio alert.")
            finally:
                self._queue.task_done()

    @staticmethod
    def _wav_data(frequencies: tuple[int, ...]) -> bytes:
        output = io.BytesIO()
        frames_per_tone = int(SAMPLE_RATE * TONE_SECONDS)
        with wave.open(output, "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(SAMPLE_RATE)
            for frequency in frequencies:
                samples = bytearray()
                for sample in range(frames_per_tone):
                    envelope = min(1.0, sample / 100, (frames_per_tone - sample) / 100)
                    value = int(3500 * envelope * math.sin(2 * math.pi * frequency * sample / SAMPLE_RATE))
                    samples.extend(struct.pack("<h", value))
                stream.writeframes(bytes(samples))
        return output.getvalue()

    def _play(self, kind: str) -> None:
        aplay = shutil.which("aplay")
        if aplay is None:
            raise RuntimeError("aplay is required for hardware audio alerts.")
        command = [aplay, "-q"]
        if self.device and self.device != "default":
            command.extend(["-D", self.device])
        subprocess.run(
            command,
            input=self._wav_data(FREQUENCIES[kind]),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=True,
        )


AUDIO_ALERTS = AudioAlerts()
