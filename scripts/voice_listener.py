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


def _parse_transcript(transcript: str, wake_phrase: str = WAKE_PHRASE) -> str:
    """Return a command only when the transcript contains the configured wake phrase."""
    lowered = transcript.lower()
    wake_at = lowered.find(wake_phrase.lower())
    if wake_at < 0:
        return ""
    return transcript[wake_at + len(wake_phrase):].strip(" ,.!?:;")


def _queue_audio(frames: queue.Queue, indata, status=None) -> None:
    if status:
        print(f"[voice] audio input status: {status}", flush=True)
    try:
        frames.put_nowait(indata[:, 0].tobytes())
    except queue.Full:
        print("[voice] audio buffer full; dropping microphone frame.", flush=True)


def route_transcript(transcript: str, send_command=_send_command, wake_phrase: str = WAKE_PHRASE) -> bool:
    command = _parse_transcript(transcript, wake_phrase)
    if not command:
        return False
    send_command(command)
    return True


def _listen(frames, vad, transcribe_samples, sd, stop_event) -> None:
    def enqueue_audio(indata, frame_count, time_info, status):
        del frame_count, time_info
        _queue_audio(frames, indata, status)

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=FRAME_SAMPLES,
        callback=enqueue_audio,
    ):
        active_frames = []
        silence_frames = 0
        speech_started = False
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
                command = _parse_transcript(transcript)
                if not command:
                    print("[voice] wake phrase heard; say a command after it.", flush=True)
                    continue
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

    from skills.voice_input.skill import transcribe_samples

    frames: queue.Queue = queue.Queue(maxsize=256)
    vad = webrtcvad.Vad(2)

    print(
        f"[voice] microphone active; waiting for wake phrase {WAKE_PHRASE!r}. "
        "Say the wake phrase and command in one utterance. Audio stays in memory and is discarded after transcription.",
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
