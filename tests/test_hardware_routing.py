import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

audio_play = importlib.import_module("skills.audio_play.skill")
camera_record = importlib.import_module("skills.camera_record.skill")


def test_local_audio_starts_detached_process(monkeypatch):
    observed = {}

    class FakeProcess:
        pid = 1234

        @staticmethod
        def poll():
            return None

    def fake_popen(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(
        audio_play.shutil,
        "which",
        lambda name: {"mpv": "mpv", "aplay": "aplay"}.get(name),
    )
    monkeypatch.setattr(
        audio_play.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="card 0: Test [Test], device 0: Output [Output]\n",
            stderr="",
        ),
    )
    monkeypatch.setattr(audio_play.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(audio_play.time, "sleep", lambda _seconds: None)
    result = audio_play.run("https://example.test/audio", target_device="local_host")

    assert result["pid"] == 1234
    assert result["backend"] == "alsa"
    assert observed["kwargs"]["start_new_session"] is True
    assert "--no-video" in observed["command"]
    assert "--ao=alsa" in observed["command"]


def test_local_audio_rejects_missing_alsa_sink(monkeypatch):
    monkeypatch.setattr(
        audio_play.shutil,
        "which",
        lambda name: {"mpv": "mpv", "aplay": "aplay"}.get(name),
    )
    monkeypatch.setattr(
        audio_play.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="",
        ),
    )

    with pytest.raises(RuntimeError, match="No ALSA sound cards"):
        audio_play.run("https://example.test/audio", target_device="local_host")


def test_local_audio_rejects_player_that_exits_immediately(monkeypatch):
    class ExitedProcess:
        pid = 1234

        @staticmethod
        def poll():
            return 1

        @staticmethod
        def communicate(timeout):
            assert timeout == 1
            return "", "No audio device"

    monkeypatch.setattr(
        audio_play.shutil,
        "which",
        lambda name: {"mpv": "mpv", "aplay": "aplay"}.get(name),
    )
    monkeypatch.setattr(
        audio_play.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="card 0: Test [Test], device 0: Output [Output]\n",
            stderr="",
        ),
    )
    monkeypatch.setattr(audio_play.subprocess, "Popen", lambda *_args, **_kwargs: ExitedProcess())
    monkeypatch.setattr(audio_play.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="mpv exited before playback"):
        audio_play.run("https://example.test/audio", target_device="local_host")


def test_camera_snapshot_uses_camera_service_frames(monkeypatch, tmp_path: Path):
    output = tmp_path / "capture.jpg"
    monkeypatch.setattr(camera_record, "WORKSPACE", SimpleNamespace(work_dir=tmp_path))
    monkeypatch.setattr(camera_record, "task_output_path", lambda *args: output)
    monkeypatch.setattr(
        "agent.tools.vision_capture.camera_snapshot",
        lambda *, output_path: output.write_bytes(b"\xff\xd8frame\xff\xd9") or output,
    )

    result = camera_record.run("snapshot")

    assert result["bytes"] == len(b"\xff\xd8frame\xff\xd9")


def test_host_camera_video_reports_browser_alternative():
    with pytest.raises(RuntimeError, match="browser camera recorder"):
        camera_record.run("video", duration_seconds=1)
