import importlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

audio_play = importlib.import_module("skills.audio_play.skill")
camera_record = importlib.import_module("skills.camera_record.skill")


def test_local_audio_starts_detached_process(monkeypatch):
    observed = {}

    class FakeProcess:
        pid = 1234

    def fake_popen(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(audio_play.shutil, "which", lambda _: "mpv")
    monkeypatch.setattr(audio_play.subprocess, "Popen", fake_popen)
    result = audio_play.run("https://example.test/audio", target_device="local_host")

    assert result["pid"] == 1234
    assert observed["kwargs"]["start_new_session"] is True
    assert "--no-video" in observed["command"]


def test_camera_capture_releases_process_after_success(monkeypatch, tmp_path: Path):
    output = tmp_path / "capture.jpg"
    observed = {"closed": False}

    class FakeProcess:
        returncode = 0

        def __enter__(self):
            return self

        def __exit__(self, *_):
            observed["closed"] = True

        def communicate(self, timeout):
            output.write_bytes(b"image")
            return "", ""

    monkeypatch.setattr(camera_record.shutil, "which", lambda _: "ffmpeg")
    monkeypatch.setattr(camera_record, "WORKSPACE", SimpleNamespace(work_dir=tmp_path))
    monkeypatch.setattr(camera_record, "task_output_path", lambda *args: output)
    monkeypatch.setattr(camera_record.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    result = camera_record.run("snapshot")

    assert result["bytes"] == len(b"image")
    assert observed["closed"]


def test_camera_capture_kills_and_releases_timed_out_process(monkeypatch, tmp_path: Path):
    observed = {"closed": False, "killed": False, "calls": 0}

    class FakeProcess:
        returncode = -9

        def __enter__(self):
            return self

        def __exit__(self, *_):
            observed["closed"] = True

        def communicate(self, timeout=None):
            observed["calls"] += 1
            if observed["calls"] == 1:
                raise subprocess.TimeoutExpired("ffmpeg", timeout)
            return "", ""

        def kill(self):
            observed["killed"] = True

    monkeypatch.setattr(camera_record.shutil, "which", lambda _: "ffmpeg")
    monkeypatch.setattr(camera_record, "task_output_path", lambda *args: tmp_path / "capture.mp4")
    monkeypatch.setattr(camera_record.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    with pytest.raises(RuntimeError, match="Camera capture timed out"):
        camera_record.run("video", duration_seconds=1)

    assert observed["killed"]
    assert observed["calls"] == 2
    assert observed["closed"]
