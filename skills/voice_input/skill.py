import json
import os
import wave
import time
from pathlib import Path


_MODELS = {}


def _model_path() -> Path:
    configured = os.getenv("HSX_VOICE_MODEL_PATH", "models/vosk-model-small-en-us-0.15")
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    if not path.is_dir():
        raise RuntimeError(f"Voice model directory not found: {path}")
    return path


def _load_model():
    try:
        from vosk import Model
    except ImportError as error:
        raise RuntimeError("Install optional voice dependencies with: pip install -r requirements-voice.txt") from error
    path = _model_path()
    key = str(path)
    if key not in _MODELS:
        _MODELS[key] = Model(key)
    return _MODELS[key]


def transcribe_samples(samples, model_name: str | None = None) -> str:
    del model_name
    try:
        from vosk import KaldiRecognizer
    except ImportError as error:
        raise RuntimeError("Install optional voice dependencies with: pip install -r requirements-voice.txt") from error
    audio = samples if isinstance(samples, bytes) else bytes(samples)
    if not audio:
        raise ValueError("No audio samples were provided.")
    recognizer = KaldiRecognizer(_load_model(), 16000)
    recognizer.AcceptWaveform(audio)
    result = json.loads(recognizer.FinalResult())
    return str(result.get("text", "")).strip()


def run(audio_path: str, model_name: str | None = None) -> dict:
    del model_name
    path = Path(audio_path)
    if not path.is_file():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
    with wave.open(str(path), "rb") as audio_file:
        if audio_file.getnchannels() != 1 or audio_file.getsampwidth() != 2 or audio_file.getframerate() != 16000:
            raise ValueError("Voice input must be mono 16-bit PCM audio at 16000 Hz.")
        transcription = transcribe_samples(audio_file.readframes(audio_file.getnframes()))
    return {"success": True, "text": transcription}


def microphone_test(duration_seconds: float = 2.0) -> dict:
    """Capture a short local sample and report whether the microphone produces audio."""
    try:
        import sounddevice as sd
    except ImportError as error:
        raise RuntimeError("Install optional voice dependencies with: pip install -r requirements-voice.txt") from error
    frames: list[bytes] = []

    def callback(indata, frame_count, time_info, status):
        del frame_count, time_info
        if status:
            raise RuntimeError(str(status))
        frames.append(indata[:, 0].tobytes())

    with sd.InputStream(samplerate=16000, channels=1, dtype="int16", callback=callback):
        time.sleep(max(0.1, duration_seconds))
    byte_count = sum(len(frame) for frame in frames)
    return {"success": byte_count > 0, "bytes": byte_count, "duration_seconds": duration_seconds}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Hsx local voice utilities")
    parser.add_argument("--test-microphone", action="store_true")
    args = parser.parse_args()
    if args.test_microphone:
        print(microphone_test())
