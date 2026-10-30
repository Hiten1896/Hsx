import importlib.util
import queue
from pathlib import Path


def _module():
    path = Path(__file__).parents[1] / "scripts" / "voice_listener.py"
    spec = importlib.util.spec_from_file_location("hsx_voice_listener", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Channel:
    def __init__(self, payload: bytes):
        self.payload = payload

    def tobytes(self) -> bytes:
        return self.payload


class _Audio:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __getitem__(self, key):
        assert key == (slice(None), 0)
        return _Channel(self.payload)


def test_wake_phrase_parser_routes_only_commands():
    module = _module()
    assert module._parse_transcript("Hey Hsx make a note") == "make a note"
    assert module._parse_transcript("ambient speech") == ""
    sent = []
    assert module.route_transcript("hey hsx create a report", sent.append)
    assert sent == ["create a report"]


def test_audio_callback_queues_pcm_bytes():
    module = _module()
    frames = queue.Queue(maxsize=1)
    module._queue_audio(frames, _Audio(b"pcm"))
    assert frames.get_nowait() == b"pcm"


def test_audio_callback_drops_when_buffer_is_full():
    module = _module()
    frames = queue.Queue(maxsize=1)
    frames.put(b"existing")
    module._queue_audio(frames, _Audio(b"dropped"))
    assert frames.get_nowait() == b"existing"
