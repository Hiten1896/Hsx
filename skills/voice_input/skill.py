from pathlib import Path


_MODELS = {}


def _load_model(model_name: str):
    try:
        import whisper
    except ImportError as error:
        raise RuntimeError("Install optional voice dependencies with: pip install -r requirements-voice.txt") from error
    if model_name not in _MODELS:
        _MODELS[model_name] = whisper.load_model(model_name)
    return _MODELS[model_name]


def transcribe_samples(samples, model_name: str = "base") -> str:
    import numpy as np

    audio = np.asarray(samples, dtype=np.float32)
    if audio.size == 0:
        raise ValueError("No audio samples were provided.")
    peak = float(np.max(np.abs(audio)))
    if peak > 1:
        audio = audio / 32768.0
    result = _load_model(model_name).transcribe(audio, fp16=False)
    return str(result.get("text", "")).strip()


def run(audio_path: str, model_name: str = "base") -> dict:
    import numpy as np
    try:
        import whisper
    except ImportError as error:
        raise RuntimeError("Install optional voice dependencies with: pip install -r requirements-voice.txt") from error

    path = Path(audio_path)
    if not path.is_file():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
    samples = whisper.load_audio(str(path))
    transcription = transcribe_samples(np.asarray(samples), model_name)
    return {"success": True, "text": transcription}
