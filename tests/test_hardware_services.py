import base64
import io
import json
import os
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.hardware.audio_alerts import AudioAlerts
from agent.hardware.camera_stream import CameraStream, _validate_rtsp_url
from agent.hardware.motion_monitor import MotionMonitor
from agent.hardware.speaker import Speaker
from agent.llm.providers import ProviderError, assert_payload_valid, to_gemini_payload, to_openai_payload
from agent.llm.types import LLMRequest
from agent.tools import vision_capture


def test_camera_stream_is_low_resolution_and_requires_local_rtsp_target():
    stream = CameraStream(rtsp_url="rtsp://192.168.10.12:8554/cam")
    command = stream.command("ffmpeg")

    assert command[command.index("-video_size") + 1] == "640x480"
    assert command[command.index("-framerate") + 1] == "15"
    assert command[command.index("-preset") + 1] == "ultrafast"
    assert command[-1] == "rtsp://192.168.10.12:8554/cam"
    assert _validate_rtsp_url("rtsp://camera.local:8554/cam")
    with pytest.raises(ValueError, match="local/private"):
        _validate_rtsp_url("rtsp://198.51.100.10:8554/cam")
    with pytest.raises(ValueError, match="credentials"):
        _validate_rtsp_url("rtsp://user:password@192.168.1.2/cam")


def test_motion_monitor_writes_headless_config_and_bounds_snapshots(tmp_path):
    snapshot_dir = tmp_path / "snapshots"
    monitor = MotionMonitor(
        snapshot_dir=snapshot_dir,
        events_path=tmp_path / "motion_events.json",
        config_path=tmp_path / "motion.conf",
        max_snapshots=10,
    )
    monitor._write_config()
    config = (tmp_path / "motion.conf").read_text(encoding="utf-8")
    assert "daemon off" in config
    assert "webcontrol_port 0" in config
    assert "stream_port 0" in config
    assert "framerate 5" in config

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for index in range(12):
        (snapshot_dir / f"snapshot-{index}.jpg").write_bytes(b"jpeg")
    monitor._record_new_snapshots()

    events = json.loads((tmp_path / "motion_events.json").read_text(encoding="utf-8"))
    assert len(list(snapshot_dir.glob("*.jpg"))) == 10
    assert len(events) == 10
    assert events[0]["event"] == "motion_snapshot"


def test_vision_capture_requires_opt_in_and_atomically_writes_jpeg(tmp_path, monkeypatch):
    monkeypatch.setenv("HSX_ENABLE_VISION_CAPTURE", "1")
    destination = tmp_path / "snapshots" / "latest.jpg"
    monkeypatch.setattr(vision_capture.shutil, "which", lambda binary: "ffmpeg" if binary == "ffmpeg" else None)

    def fake_run(command, **kwargs):
        Path(command[-1]).write_bytes(b"\xff\xd8frame-data\xff\xd9")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(vision_capture.subprocess, "run", fake_run)
    result = vision_capture.capture_frame(output_path=destination)

    assert result == destination
    assert destination.read_bytes().startswith(b"\xff\xd8")
    if os.name == "posix":
        assert oct(destination.stat().st_mode & 0o777) == "0o600"
    assert not list(destination.parent.glob(".latest-*.jpg"))


def test_vision_capture_rejects_disabled_or_invalid_output(tmp_path, monkeypatch):
    monkeypatch.delenv("HSX_ENABLE_VISION_CAPTURE", raising=False)
    with pytest.raises(RuntimeError, match="disabled"):
        vision_capture.capture_frame(output_path=tmp_path / "latest.jpg")

    monkeypatch.setenv("HSX_ENABLE_VISION_CAPTURE", "1")
    monkeypatch.setattr(vision_capture.shutil, "which", lambda _: "ffmpeg")
    def fake_run(command, **kwargs):
        Path(command[-1]).write_bytes(b"not-a-jpeg")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(vision_capture.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="JPEG"):
        vision_capture.capture_frame(output_path=tmp_path / "latest.jpg")


def test_speaker_and_alert_tones_use_bounded_alsa_payloads(tmp_path, monkeypatch):
    model = tmp_path / "voice.onnx"
    model.touch()
    speaker = Speaker(model_path=model)
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if "--output_file" in command:
            Path(command[command.index("--output_file") + 1]).write_bytes(b"wav")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("agent.hardware.speaker.shutil.which", lambda _: "binary")
    monkeypatch.setattr("agent.hardware.speaker.subprocess.run", fake_run)
    speaker._speak("Task completed")
    assert commands[0][0] == "binary"
    assert commands[1][0] == "binary"
    assert not list(tmp_path.glob("hsx-voice-*.wav"))

    monkeypatch.setattr("agent.hardware.audio_alerts.shutil.which", lambda _: "aplay")
    alert = AudioAlerts()
    played = []
    monkeypatch.setattr(
        "agent.hardware.audio_alerts.subprocess.run",
        lambda command, **kwargs: played.append((command, kwargs["input"])),
    )
    audio = alert._wav_data((880,))
    with wave.open(io.BytesIO(audio)) as stream:
        assert stream.getframerate() == 16_000
    alert._play("success")
    assert played[0][0][0] == "aplay"


def test_gemini_accepts_bounded_inline_camera_frames_only():
    encoded = base64.b64encode(b"\xff\xd8frame\xff\xd9").decode("ascii")
    request = LLMRequest([{
        "role": "user",
        "content": [
            {"type": "text", "text": "Describe this frame."},
            {"type": "image", "mime_type": "image/jpeg", "data": encoded},
        ],
    }])
    payload = to_gemini_payload(request)
    assert payload["contents"][0]["parts"][1] == {
        "inlineData": {"mimeType": "image/jpeg", "data": encoded},
    }
    assert_payload_valid("gemini", payload)
    with pytest.raises(ProviderError, match="only by the Gemini"):
        to_openai_payload(request, "model")


def test_gemini_rejects_oversized_or_invalid_image_payloads():
    with pytest.raises(ValueError, match="base64"):
        assert_payload_valid("gemini", {
            "contents": [{
                "role": "user",
                "parts": [{"inlineData": {"mimeType": "image/jpeg", "data": "not-base64"}}],
            }],
        })
    with pytest.raises(ValueError, match="2 MiB"):
        assert_payload_valid("gemini", {
            "contents": [{
                "role": "user",
                "parts": [{
                    "inlineData": {
                        "mimeType": "image/jpeg",
                        "data": base64.b64encode(b"x" * (2 * 1024 * 1024 + 1)).decode("ascii"),
                    },
                }],
            }],
        })


def test_visual_orchestrator_uses_ephemeral_image_and_requires_approval(tmp_path, monkeypatch):
    from agent import orchestrator
    from agent.llm_client import NormalizedBlock, NormalizedResponse

    frame = tmp_path / "latest.jpg"
    frame.write_bytes(b"\xff\xd8frame\xff\xd9")
    monkeypatch.setenv("HSX_ENABLE_VISION_CAPTURE", "1")
    monkeypatch.setattr(vision_capture, "capture_frame", lambda: frame)
    monkeypatch.setattr(orchestrator.POLICY, "decide", lambda *args: {"allowed": True})
    monkeypatch.setattr(orchestrator.MEMORY, "prompt_context", lambda _: "")
    monkeypatch.setattr(orchestrator.MEMORY, "record_message", lambda *args: None)
    monkeypatch.setattr(orchestrator.EXPERIENCE_MEMORY, "prompt_context", lambda _: "")
    monkeypatch.setattr(orchestrator.RAG_INDEX, "start_ingestion", lambda: False)
    monkeypatch.setattr(orchestrator.RAG_INDEX, "prompt_context", lambda _: "")
    monkeypatch.setattr(orchestrator, "system_body_context", lambda _: "")
    monkeypatch.setattr(orchestrator.POLICY, "is_stopped", lambda: False)
    captured = {}

    def fake_call_llm(history, tools, memory, **kwargs):
        captured["history"] = history
        captured["kwargs"] = kwargs
        return NormalizedResponse([NormalizedBlock(type="text", text="A desk is visible.")])

    monkeypatch.setattr(orchestrator, "call_llm", fake_call_llm)
    history = []
    answer, result_history = orchestrator.run_agent(
        "What do you see?",
        history,
        [],
        {},
    )

    assert answer == "A desk is visible."
    assert captured["kwargs"]["required_provider"] == "gemini"
    assert any(block.get("type") == "image" for block in captured["history"][-1]["content"])
    assert all(
        block.get("type") != "image"
        for message in result_history
        for block in message.get("content", [])
        if isinstance(block, dict)
    )


def test_visual_orchestrator_does_not_capture_without_policy_approval(monkeypatch):
    from agent import orchestrator

    captured = []
    monkeypatch.setenv("HSX_ENABLE_VISION_CAPTURE", "1")
    monkeypatch.setattr(vision_capture, "capture_frame", lambda: captured.append(True))
    monkeypatch.setattr(orchestrator.POLICY, "decide", lambda *args: {"allowed": False})
    monkeypatch.setattr(orchestrator.MEMORY, "record_message", lambda *args: None)

    answer, _ = orchestrator.run_agent("What do you see?", [], [], {})

    assert "confirmation" in answer
    assert captured == []


def test_anomaly_monitor_emits_audio_callback_for_detected_anomaly():
    import threading

    from agent.ml.anomaly import AnomalyMonitor, SystemMetrics

    received = threading.Event()

    class FakeDetector:
        def observe(self, _metrics):
            return {"kind": "system_anomaly"}

    class FakeCollector:
        def sample(self):
            return SystemMetrics(99, 99, None, 1)

    monitor = AnomalyMonitor(
        detector=FakeDetector(),
        collector=FakeCollector(),
        interval_seconds=0.01,
        on_anomaly=lambda _event: received.set(),
    )
    monitor.start()
    assert received.wait(1)
    monitor.stop(timeout=1)


def test_speaker_and_tone_queues_accept_multiple_async_alerts(tmp_path, monkeypatch):
    from agent.hardware import audio_alerts, speaker

    model = tmp_path / "voice.onnx"
    model.touch()
    voice = speaker.Speaker(model_path=model)
    played_voice = []
    monkeypatch.setattr(voice, "enabled", lambda: True)
    monkeypatch.setattr(voice, "_speak", played_voice.append)
    monkeypatch.setattr(speaker.shutil, "which", lambda _: "audio-tool")
    assert voice.speak_async("first")
    assert voice.speak_async("second")
    voice._queue.join()
    voice.stop()
    assert played_voice == ["first", "second"]

    tones = audio_alerts.AudioAlerts()
    played_tones = []
    monkeypatch.setattr(tones, "enabled", lambda: True)
    monkeypatch.setattr(tones, "_play", played_tones.append)
    assert tones.play_async("success")
    assert tones.play_async("warning")
    tones._queue.join()
    tones.stop()
    assert played_tones == ["success", "warning"]
