import base64
import io
import json
import os
import wave
from pathlib import Path
from types import SimpleNamespace
from time import monotonic

import pytest

from agent.hardware.audio_alerts import AudioAlerts
from agent.hardware.camera import CameraFrame, CameraService
from agent.hardware.camera_stream import CameraStream
from agent.hardware.motion_monitor import MotionMonitor
from agent.hardware.speaker import Speaker
from agent.llm.providers import ProviderError, assert_payload_valid, to_gemini_payload, to_openai_payload
from agent.llm.types import LLMRequest
from agent.tools import vision_capture


def test_legacy_camera_stream_delegates_to_single_camera_service():
    stream = CameraStream()
    command = stream.command("ffmpeg")

    assert command[command.index("-video_size") + 1] == "640x480"
    assert command[command.index("-framerate") + 1] == "15"
    assert command[-1] == "pipe:1"
    with pytest.raises(ValueError, match="removed"):
        CameraStream(rtsp_url="rtsp://192.168.10.12:8554/cam")


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


def test_camera_stop_terminates_supervised_ffmpeg_process(monkeypatch):
    import threading

    process_holder = []

    class BlockingPipe:
        def __init__(self):
            self.stopped = threading.Event()

        def read(self, _size):
            self.stopped.wait(2)
            return b""

    class FakeProcess:
        def __init__(self):
            self.stdout = BlockingPipe()
            self.return_code = None

        def poll(self):
            return self.return_code

        def terminate(self):
            self.return_code = 0
            self.stdout.stopped.set()

        def wait(self, timeout=None):
            del timeout
            self.return_code = self.return_code or 0
            return self.return_code

        def kill(self):
            self.return_code = -9
            self.stdout.stopped.set()

    def make_process(*_args, **_kwargs):
        process = FakeProcess()
        process_holder.append(process)
        return process

    monkeypatch.setattr("agent.hardware.camera.shutil.which", lambda _program: "ffmpeg")
    monkeypatch.setattr("agent.hardware.camera.os.path.exists", lambda _path: True)
    monkeypatch.setattr("agent.hardware.camera.subprocess.Popen", make_process)
    camera = CameraService(device="/dev/video0")
    camera.start()
    for _ in range(100):
        if process_holder:
            break
        threading.Event().wait(0.01)
    camera.stop()
    assert process_holder
    assert len(process_holder) == 1
    assert process_holder[0].poll() is not None


def test_mjpeg_api_streams_without_an_agent_job(monkeypatch):
    import threading
    from http.server import ThreadingHTTPServer
    from time import monotonic
    from urllib.request import Request, urlopen

    import server

    jpeg = b"\xff\xd8dashboard-frame\xff\xd9"

    class FakeCamera:
        def has_owner(self, _owner):
            return True

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
        origin = f"http://127.0.0.1:{httpd.server_port}"
        monkeypatch.setattr(server, "ALLOWED_ORIGINS", (origin,))
        request = Request(
            f"{origin}/api/camera/stream?session_id=test",
            headers={"Origin": origin},
        )
        with urlopen(request, timeout=3) as response:
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


def test_camera_status_is_read_only_and_does_not_start_camera(monkeypatch):
    import server
    from tests.test_files_panel import _request

    class FakeCamera:
        started = False

        def start(self):
            self.started = True

        def is_started(self):
            return self.started

        def health_check(self):
            return {"running": self.started, "device_available": True}

    camera = FakeCamera()
    monkeypatch.setattr(server, "CAMERA", camera)
    status, _, body = _request("/api/camera/status")
    assert status == 200
    assert json.loads(body)["running"] is False
    assert camera.started is False


def test_camera_consent_is_bound_to_session_start_and_stop(monkeypatch):
    import server
    from agent.hardware.camera import camera_session_owner
    from tests.test_files_panel import _request

    class FakeCamera:
        owners = set()

        def acquire(self, owner):
            self.owners.add(owner)
            return True

        def release(self, owner):
            self.owners.discard(owner)

        def has_owner(self, owner):
            return owner in self.owners

        def is_started(self):
            return bool(self.owners)

        def health_check(self):
            return {"running": bool(self.owners), "device_available": True}

    camera = FakeCamera()
    monkeypatch.setattr(server, "CAMERA", camera)

    status, _, _ = _request("/api/camera/start", method="POST", payload={"session_id": "browser-one"})
    assert status == 202
    assert camera.has_owner(camera_session_owner("browser-one"))

    status, _, body = _request("/api/camera/stop", method="POST", payload={"session_id": "browser-two"})
    assert status == 200
    assert camera.has_owner(camera_session_owner("browser-one"))

    status, _, _ = _request("/api/camera/stop", method="POST", payload={"session_id": "browser-one"})
    assert status == 200
    assert not camera.has_owner(camera_session_owner("browser-one"))





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
    frame = CameraFrame(1, b"\xff\xd8frame-data\xff\xd9", monotonic())
    monkeypatch.setattr(vision_capture.CAMERA, "health_check", lambda: {"device_available": True})
    monkeypatch.setattr(vision_capture.CAMERA, "is_started", lambda: True)
    monkeypatch.setattr(vision_capture.CAMERA, "latest_frame", lambda: frame)
    monkeypatch.setattr(vision_capture.CAMERA, "wait_for_frame", lambda *_args, **_kwargs: frame)
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
    monkeypatch.setattr(vision_capture.CAMERA, "health_check", lambda: {"device_available": True})
    monkeypatch.setattr(vision_capture.CAMERA, "is_started", lambda: True)
    monkeypatch.setattr(vision_capture.CAMERA, "latest_frame", lambda: CameraFrame(1, b"invalid", monotonic()))
    monkeypatch.setattr(vision_capture.CAMERA, "wait_for_frame", lambda *_args, **_kwargs: CameraFrame(1, b"invalid", monotonic()))
    with pytest.raises(RuntimeError, match="JPEG"):
        vision_capture.capture_frame(output_path=tmp_path / "latest.jpg")


def test_vision_capture_uses_shared_camera_without_reopening_device(tmp_path, monkeypatch):
    monkeypatch.setenv("HSX_ENABLE_VISION_CAPTURE", "1")
    frame = CameraFrame(2, b"\xff\xd8shared-frame\xff\xd9", 0.0)
    monkeypatch.setattr(vision_capture.CAMERA, "is_started", lambda: True)
    monkeypatch.setattr(vision_capture.CAMERA, "latest_frame", lambda: CameraFrame(1, b"", 0.0))
    monkeypatch.setattr(vision_capture.CAMERA, "wait_for_frame", lambda *_args, **_kwargs: frame)
    destination = tmp_path / "snapshots" / "latest.jpg"
    assert vision_capture.capture_frame(output_path=destination) == destination
    assert destination.read_bytes() == frame.data


def test_camera_snapshot_reuses_live_stream_without_approval(tmp_path, monkeypatch):
    from agent.tools import vision_capture

    jpeg = b"\xff\xd8live-frame\xff\xd9"
    calls = []
    frame = CameraFrame(7, jpeg, monotonic())
    monkeypatch.setattr(vision_capture.CAMERA, "is_started", lambda: True)
    monkeypatch.setattr(vision_capture.CAMERA, "latest_frame", lambda: frame)
    monkeypatch.setattr(vision_capture.CAMERA, "wait_for_frame", lambda *_args, **_kwargs: frame)
    monkeypatch.setattr(vision_capture.CAMERA, "start", lambda: calls.append("start"))
    monkeypatch.setattr(vision_capture.CAMERA, "stop", lambda: calls.append("stop"))
    result = vision_capture.camera_snapshot(output_path=tmp_path / "live.jpg")
    assert result.read_bytes() == jpeg
    assert calls == []


def test_camera_snapshot_starts_and_stops_shared_service_when_off(tmp_path, monkeypatch):
    from agent.tools import vision_capture

    jpeg = b"\xff\xd8brief-frame\xff\xd9"
    frame = CameraFrame(1, jpeg, monotonic())
    state = {"running": False}
    calls = []
    monkeypatch.setenv("HSX_ENABLE_VISION_CAPTURE", "1")
    monkeypatch.setattr(vision_capture.CAMERA, "health_check", lambda: {"device_available": True})
    monkeypatch.setattr(vision_capture.CAMERA, "is_started", lambda: state["running"])
    monkeypatch.setattr(vision_capture.CAMERA, "latest_frame", lambda: None)
    monkeypatch.setattr(vision_capture.CAMERA, "wait_for_frame", lambda *_args, **_kwargs: frame)
    monkeypatch.setattr(vision_capture.CAMERA, "acquire", lambda _owner: (calls.append("start"), state.update(running=True)))
    monkeypatch.setattr(vision_capture.CAMERA, "release", lambda _owner: (calls.append("stop"), state.update(running=False)))
    result = vision_capture.camera_snapshot(output_path=tmp_path / "brief.jpg")
    assert result.read_bytes() == jpeg
    assert calls == ["start", "stop"]
    assert state["running"] is False


def test_camera_disabled_reason_names_setting(monkeypatch):
    from agent.tools import vision_capture

    monkeypatch.delenv("HSX_ENABLE_VISION_CAPTURE", raising=False)
    assert "HSX_ENABLE_VISION_CAPTURE=1" in vision_capture.disabled_reason()


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


def test_visual_orchestrator_uses_live_frame_after_approval(tmp_path, monkeypatch):
    import os
    import shutil

    from agent import orchestrator
    from agent.hardware.camera import CAMERA
    from agent.notifier import NOTIFIER
    from agent.llm_client import NormalizedBlock, NormalizedResponse

    jpeg = b"\xff\xd8frame\xff\xd9"
    events = []
    monkeypatch.setenv("HSX_ENABLE_VISION_CAPTURE", "1")
    monkeypatch.setattr(shutil, "which", lambda _name: "ffmpeg")
    monkeypatch.setattr(os.path, "exists", lambda _path: True)
    monkeypatch.setattr(CAMERA, "health_check", lambda: {"device_available": True, "running": True})
    monkeypatch.setattr(CAMERA, "has_owner", lambda _owner: False)
    monkeypatch.setattr(CAMERA, "latest_frame", lambda: None)
    monkeypatch.setattr(CAMERA, "acquire", lambda _owner: True)
    monkeypatch.setattr(CAMERA, "wait_for_frame", lambda *_args, **_kwargs: CameraFrame(1, jpeg, monotonic()))
    monkeypatch.setattr(CAMERA, "release", lambda _owner: None)
    monkeypatch.setattr(NOTIFIER, "broadcast_event", lambda *args: None)
    monkeypatch.setattr(orchestrator.POLICY, "decide", lambda *args: {"allowed": True})
    monkeypatch.setattr(orchestrator.POLICY, "audit_event", lambda *args: None)
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
        on_activity_event=lambda event_type, payload: events.append((event_type, payload)),
    )

    assert answer.startswith("A desk is visible.")
    assert any(event == "camera_state" and payload["active"] for event, payload in events)
    assert any(event == "camera_snapshot" for event, _payload in events)
    assert captured["kwargs"]["required_provider"] == "gemini"
    assert any(block.get("type") == "image" for block in captured["history"][-1]["content"])
    assert all(
        block.get("type") != "image"
        for message in result_history
        for block in message.get("content", [])
        if isinstance(block, dict)
    )


def test_camera_start_pauses_for_approval_without_opening_camera(monkeypatch):
    import os
    import shutil

    from agent.ask_user import ApprovalRequest
    from agent import orchestrator
    from agent.hardware.camera import CAMERA

    monkeypatch.setenv("HSX_ENABLE_VISION_CAPTURE", "1")
    monkeypatch.setattr(shutil, "which", lambda _name: "ffmpeg")
    monkeypatch.setattr(os.path, "exists", lambda _path: True)
    monkeypatch.setattr(CAMERA, "health_check", lambda: {"device_available": True})
    opened = []
    monkeypatch.setattr(CAMERA, "acquire", lambda _owner: opened.append(True))
    monkeypatch.setattr(
        orchestrator.POLICY,
        "decide",
        lambda *args: {
            "allowed": False,
            "status": "waiting_approval",
            "approval_request": {"request_type": "tool_consent", "why": "Camera access requires approval."},
        },
    )
    monkeypatch.setattr(orchestrator.MEMORY, "record_message", lambda *args: None)

    with pytest.raises(ApprovalRequest) as paused:
        orchestrator.run_agent("What do you see?", [], [], {})

    assert paused.value.tool == "vision_capture"
    assert paused.value.approval_request["request_type"] == "tool_consent"
    assert "camera" in paused.value.approval_request["why"].lower()
    assert opened == []


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
