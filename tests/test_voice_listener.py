import importlib.util
from pathlib import Path


def _voice_listener_module():
    path = Path(__file__).parents[1] / "scripts" / "voice_listener.py"
    spec = importlib.util.spec_from_file_location("hsx_voice_listener", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_voice_parser_accepts_wake_phrase_and_command():
    module = _voice_listener_module()

    assert module._parse_transcript("Hey Hsx make a note", "hey hsx", True) == ("command", "make a note")


def test_voice_parser_allows_pause_after_wake_phrase():
    module = _voice_listener_module()

    assert module._parse_transcript("hey hsx", "hey hsx", True) == ("wake", "")


def test_voice_parser_can_run_without_wake_phrase():
    module = _voice_listener_module()

    assert module._parse_transcript("create a report", "hey hsx", False) == ("command", "create a report")
