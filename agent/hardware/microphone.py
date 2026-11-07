from __future__ import annotations

import hashlib
import logging
import threading

LOGGER = logging.getLogger("hsx.hardware.microphone")
SAMPLE_RATE = 16000


def microphone_session_owner(session_id: str) -> str:
    if not isinstance(session_id, str) or not session_id or len(session_id) > 256:
        raise ValueError("Microphone consent requires a valid browser session.")
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:40]
    return f"web-ui:{digest}"


class MicrophoneService:
    """Own a live host input stream without retaining or forwarding audio."""

    def __init__(self):
        self._lock = threading.RLock()
        self._stream = None
        self._owners: set[str] = set()
        self._owner_timers: dict[str, threading.Timer] = {}
        self._last_error = ""
        self._portaudio_error_type: type[Exception] = RuntimeError

    @staticmethod
    def _discard_audio(_indata, _frames, _time_info, status) -> None:
        if status:
            LOGGER.warning("Microphone input status: %s", status)

    def acquire(self, owner: str, lease_seconds: float = 300.0) -> bool:
        if not owner or len(owner) > 80:
            raise ValueError("Microphone owner must be a short non-empty name.")
        if lease_seconds <= 0 or lease_seconds > 1800:
            raise ValueError("Microphone session lease must be between 0 and 1800 seconds.")
        with self._lock:
            if owner in self._owners:
                return False
            if self._stream is not None:
                self._owners.add(owner)
                self._schedule_release(owner, lease_seconds)
                return False
            try:
                import sounddevice
            except ImportError as error:
                message = "Host microphone needs the optional sounddevice package; install requirements-voice.txt."
                self._last_error = message
                raise RuntimeError(message) from error

            self._portaudio_error_type = sounddevice.PortAudioError
            stream = None
            try:
                stream = sounddevice.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    dtype="int16",
                    callback=self._discard_audio,
                )
                stream.start()
            except (OSError, RuntimeError, self._portaudio_error_type) as error:
                if stream is not None:
                    try:
                        stream.close()
                    except (OSError, RuntimeError, self._portaudio_error_type):
                        LOGGER.exception("Could not close the microphone stream after startup failed.")
                self._last_error = str(error) or "The host microphone could not be opened."
                raise RuntimeError(f"Host microphone did not start: {self._last_error}") from error
            self._stream = stream
            self._owners.add(owner)
            self._last_error = ""
            self._schedule_release(owner, lease_seconds)
            return True

    def _schedule_release(self, owner: str, lease_seconds: float) -> None:
        old_timer = self._owner_timers.pop(owner, None)
        if old_timer is not None:
            old_timer.cancel()
        timer = threading.Timer(lease_seconds, self._expire_owner, args=(owner,))
        timer.daemon = True
        self._owner_timers[owner] = timer
        timer.start()

    def _expire_owner(self, owner: str) -> None:
        try:
            self.release(owner)
        except (OSError, RuntimeError, self._portaudio_error_type):
            LOGGER.exception("Could not stop the expired microphone session.")

    def release(self, owner: str) -> bool:
        with self._lock:
            self._owners.discard(owner)
            timer = self._owner_timers.pop(owner, None)
            if timer is not None:
                timer.cancel()
            if self._owners or self._stream is None:
                return False
            stream = self._stream
            failure = None
            try:
                stream.stop()
            except (OSError, RuntimeError, self._portaudio_error_type) as error:
                failure = error
            try:
                stream.close()
            except (OSError, RuntimeError, self._portaudio_error_type) as error:
                failure = failure or error
            if failure is not None:
                self._last_error = str(failure) or "The host microphone did not confirm shutdown."
                raise RuntimeError(f"Could not confirm microphone shutdown: {self._last_error}") from failure
            self._stream = None
            self._last_error = ""
            return True

    def is_started(self) -> bool:
        with self._lock:
            return self._stream is not None

    def has_owner(self, owner: str) -> bool:
        with self._lock:
            return owner in self._owners

    def health_check(self) -> dict[str, object]:
        with self._lock:
            return {
                "running": self._stream is not None,
                "owners": len(self._owners),
                "last_error": self._last_error,
            }


MICROPHONE = MicrophoneService()
