import base64
import io
import json
import os
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.hardware.audio_alerts import AudioAlerts
from agent.hardware.camera import CameraFrame, CameraService
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


def test_active_dashboard_contains_camera_panel():
    root = Path(__file__).resolve().parents[1]
    page = (root / "web" / "index.html").read_text(encoding="utf-8")
    client = (root / "web" / "chat.js").read_text(encoding="utf-8")
    contract = json.loads((root / "web" / "api-contract.json").read_text(encoding="utf-8"))

    assert 'id="live-camera"' in page
    assert 'id="camera-status"' in page
    assert "apiContract.endpoints.camera_stream" in client
    assert contract["endpoints"]["camera_stream"] == "/api/camera/stream"
    assert contract["endpoints"]["camera_status"] == "/api/camera/status"


def test_standalone_camera_buffers_bounded_mjpeg_frames():
    camera = CameraService(device="/dev/video0")
    first = b"\xff\xd8first\xff\xd9"
    second = b"\xff\xd8second\xff\xd9"
    camera._consume_mjpeg(io.BytesIO(b"noise" + first + second))
    frame = camera.latest_frame()

    assert camera.command("ffmpeg")[camera.command("ffmpeg").index("-framerate") + 1] == "15"
    assert frame is not None
    assert frame.version == 2
    assert frame.data == second
    assert camera.wait_for_frame(1, timeout=0.1) == frame


def test_mjpeg_api_streams_without_an_agent_job(monkeypatch):
    import threading
    from http.server import ThreadingHTTPServer
    from time import monotonic
    from urllib.request import urlopen

    import server

    jpeg = b"\xff\xd8dashboard-frame\xff\xd9"

    class FakeCamera:
        def latest_frame(self):
            return CameraFrame(1, jpeg, monotonic(), 1234.5)

        def wait_for_frame(self, _version, timeout=3.0):
            return None

        def health_check(self):
            return {"running": True}

    monkeypatch.setattr(server, "CAMERA", FakeCamera())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.HsxHandler)
    thread = threading.Thread(target=httpd.serve_forever)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{httpd.server_port}/api/camera/stream", timeout=3) as response:
            body = response.read()
            assert response.status == 200
            assert response.headers["Content-Type"] == "multipart/x-mixed-replace; boundary=frame"
        assert b"--frame\r\n" in body
        assert b"X-Hsx-Captured-At: 1234.500000\r\n\r\n" in body
        assert jpeg in body
    finally:
        httpd.shutdown()
        thread.join(timeout=3)
        httpd.server_close()





def test_motion_monitor_writes_headless_config_and_bounds_snapshots(tmp_path):
    snapshot_dir = tmp_path / "snapshots"
    published_events = []
    monitor = MotionMonitor(
        snapshot_dir=snapshot_dir,
        events_path=tmp_path / "motion_events.json",
        config_path=tmp_path / "motion.conf",
        max_snapshots=10,
        on_event=published_events.append,
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
    assert published_events == events


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


def test_vision_capture_uses_shared_camera_without_reopening_device(tmp_path, monkeypatch):
    monkeypatch.setenv("HSX_ENABLE_VISION_CAPTURE", "1")
    frame = CameraFrame(2, b"\xff\xd8shared-frame\xff\xd9", 0.0)
    monkeypatch.setattr(vision_capture.CAMERA, "is_started", lambda: True)
    monkeypatch.setattr(vision_capture.CAMERA, "latest_frame", lambda: CameraFrame(1, b"", 0.0))
    monkeypatch.setattr(vision_capture.CAMERA, "wait_for_frame", lambda *_args, **_kwargs: frame)
    monkeypatch.setattr(
        vision_capture.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("shared camera capture must not reopen /dev/video0"),
    )

    destination = tmp_path / "snapshots" / "latest.jpg"
    assert vision_capture.capture_frame(output_path=destination) == destination
    assert destination.read_bytes() == frame.data


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
