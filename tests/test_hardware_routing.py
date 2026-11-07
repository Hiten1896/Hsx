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
