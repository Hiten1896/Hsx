#!/usr/bin/env python3
import json
import os
import queue
import sys
import threading
import time
import urllib.error
from pathlib import Path
import urllib.request

from dotenv import dotenv_values, load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

SAMPLE_RATE = 16000
FRAME_SAMPLES = 480
WAKE_PHRASE = os.getenv("HSX_VOICE_WAKE_PHRASE", "hey hsx").strip().lower()
REQUIRE_WAKE_PHRASE = os.getenv("HSX_VOICE_REQUIRE_WAKE_PHRASE", "1").strip() != "0"
COMMAND_WINDOW_SECONDS = max(3.0, float(os.getenv("HSX_VOICE_COMMAND_WINDOW_SECONDS", "8")))
AUDIO_DEVICE = os.getenv("HSX_MICROPHONE_DEVICE", "").strip() or None
BASE_URL = os.getenv("HSX_URL", "http://127.0.0.1:8000").rstrip("/")


def _auth_token() -> str:
    token = os.getenv("HSX_AUTH_TOKEN", "").strip()
    if not token:
        token = str(dotenv_values(ROOT / ".env").get("HSX_AUTH_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("HSX_AUTH_TOKEN is missing from .env and the environment.")
    return token


def _send_command(message: str) -> dict:
    payload = json.dumps({
        "message": message,
        "session_id": "voice-local",
        "target_device": "local_host",
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}/api/chat",
        data=payload,
        headers={
            "Authorization": f"Bearer {_auth_token()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except (OSError, urllib.error.HTTPError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not submit voice command to HSX: {error}") from error


def _parse_transcript(transcript: str, wake_phrase: str, require_wake_phrase: bool) -> tuple[str, str]:
    text = " ".join(transcript.lower().split())
    if not text:
        return "", ""
    if not require_wake_phrase:
        return "command", transcript.strip()
    wake_at = transcript.lower().find(wake_phrase)
    if wake_at < 0:
        return "", ""
    command = transcript[wake_at + len(wake_phrase):].strip(" ,.!?:;")
    return ("wake", "") if not command else ("command", command)


def _listen(frames, vad, transcribe_samples, sd, stop_event) -> None:
    def enqueue_audio(indata, frame_count, time_info, status):
        if status:
            print(f"[voice] audio input status: {status}", flush=True)
        try:
            frames.put_nowait(indata[:, 0].tobytes())
        except queue.Full:
            print("[voice] audio buffer full; dropping microphone frame.", flush=True)

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=FRAME_SAMPLES,
        device=AUDIO_DEVICE,
        callback=enqueue_audio,
    ):
        active_frames = []
        silence_frames = 0
        speech_started = False
        command_window_deadline = 0.0
        while not stop_event.is_set():
            try:
                frame = frames.get(timeout=0.5)
            except queue.Empty:
                continue
            voiced = vad.is_speech(frame, SAMPLE_RATE)
            if voiced:
                speech_started = True
                silence_frames = 0
                active_frames.append(frame)
            elif speech_started:
                active_frames.append(frame)
                silence_frames += 1

            if speech_started and (silence_frames >= 30 or len(active_frames) >= 500):
                samples = b"".join(active_frames)
                active_frames = []
                silence_frames = 0
                speech_started = False
                try:
                    transcript = transcribe_samples(samples).strip()
                except (OSError, RuntimeError, ValueError) as error:
                    print(f"[voice] transcription failed: {error}", flush=True)
                    continue
                mode, command = _parse_transcript(transcript, WAKE_PHRASE, REQUIRE_WAKE_PHRASE)
                if mode == "wake":
                    command_window_deadline = time.monotonic() + COMMAND_WINDOW_SECONDS
                    print("[voice] wake phrase heard; listening for your command.", flush=True)
                    continue
                if mode != "command" and time.monotonic() > command_window_deadline:
                    continue
                if mode != "command":
                    command = transcript.strip()
                command_window_deadline = 0.0
                print(f"[voice] recognized command: {command}", flush=True)
                try:
                    _send_command(command)
                except RuntimeError as error:
                    print(f"[voice] {error}", flush=True)


def main() -> None:
    if os.getenv("HSX_ENABLE_VOICE_LISTENER", "").strip() != "1":
        print(
            "[voice] listener disabled; set HSX_ENABLE_VOICE_LISTENER=1 in .env to opt in.",
            flush=True,
        )
        return
    if not WAKE_PHRASE:
        raise SystemExit("Set HSX_VOICE_WAKE_PHRASE to a non-empty phrase.")

    try:
        import sounddevice as sd
        import webrtcvad
    except ImportError as error:
        raise SystemExit(
            "Install optional microphone dependencies with "
            "`pip install -r requirements-voice.txt` and install libportaudio2."
        ) from error

    from skills.voice_input.skill import _model_path, transcribe_samples

    try:
        _model_path()
        sd.query_devices(device=AUDIO_DEVICE, kind="input")
    except (OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"Voice input is not ready: {error}") from error

    frames: queue.Queue = queue.Queue(maxsize=256)
    vad = webrtcvad.Vad(2)

    print(
        f"[voice] microphone active; waiting for wake phrase {WAKE_PHRASE!r}. "
        "You may pause after the wake phrase; audio stays in memory and is discarded after transcription.",
        flush=True,
    )
    stop_event = threading.Event()
    listener = threading.Thread(
        target=_listen,
        args=(frames, vad, transcribe_samples, sd, stop_event),
        name="hsx-microphone-listener",
        daemon=True,
    )
    listener.start()
    try:
        while listener.is_alive():
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        stop_event.set()
        listener.join(timeout=2)


if __name__ == "__main__":
    main()
