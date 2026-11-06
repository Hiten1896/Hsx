import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import server
from agent.ask_user import AskUserJobs
from agent.tui import HsxTUI
from tests.test_files_panel import _request

BASE_DIR = Path(__file__).resolve().parents[1]


def test_cloud_greeting_uses_only_a_configured_cloud_provider(monkeypatch):
    providers = [SimpleNamespace(name="ollama"), SimpleNamespace(name="gemini")]
    router = SimpleNamespace(providers=providers.copy())
    calls = []

    def fake_call_llm(messages, tools, **kwargs):
        calls.append((messages, tools, kwargs))
        router.providers = [providers[1]]
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="Good morning, Sir.")])

    monkeypatch.setattr(server, "default_router", lambda: router)
    monkeypatch.setattr(server, "call_llm", fake_call_llm)

    assert server._generate_greeting("morning") == "Good morning, Sir."
    assert calls[0][2]["required_provider"] == "gemini"
    assert calls[0][2]["disable_panel"] is True
    assert "morning" in calls[0][0][0]["content"]
    assert router.providers == providers


def test_generated_artifact_filter_excludes_current_and_legacy_upload_paths():
    assert server._is_user_upload_path("Hsx Work/Uploads/screenshot.png")
    assert server._is_user_upload_path("Hsx Work/2025-01-03 1215 Uploads/screenshot.png")
    assert not server._is_user_upload_path("Hsx Work/2025-01-03 1215 Report/report.pdf")


def test_resource_notifications_fire_once_per_high_resource_episode(monkeypatch):
    sent = []

    class FakeNotifier:
        def notify(self, event, title, **kwargs):
            sent.append((event, title, kwargs))

    monkeypatch.setattr(server, "NOTIFIER", FakeNotifier())
    disk_used = [90]
    monkeypatch.setattr(
        server.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(used=disk_used[0], total=100),
    )
    monkeypatch.setattr(server, "_RESOURCE_ALERT_STATE", {"cpu": False, "memory": False, "disk": False})

    high = server.SystemMetrics(cpu_percent=99, ram_percent=95, temperature_c=None, outbound_connections=0)
    normal = server.SystemMetrics(cpu_percent=10, ram_percent=20, temperature_c=None, outbound_connections=0)
    server._notify_resource_alerts(high)
    server._notify_resource_alerts(high)
    assert len(sent) == 3

    disk_used[0] = 50
    server._notify_resource_alerts(normal)
    disk_used[0] = 90
    server._notify_resource_alerts(high)
    assert len(sent) == 6
    assert all(event == "health_alert" for event, _, _ in sent)


def test_notification_sse_disables_buffering_and_resumes_from_requested_id(monkeypatch):
    seen_after = []

    class FakeNotifier:
        def wait_for_notifications(self, after_id, timeout):
            seen_after.append((after_id, timeout))
            if len(seen_after) == 1:
                return [{
                    "id": 8,
                    "event": "job_done",
                    "title": "Task complete",
                    "job_id": "job-1",
                    "result_path": "",
                    "created": "2026-01-01T00:00:00+00:00",
                    "read": 0,
                }]
            raise BrokenPipeError

    class FakeWriter:
        def __init__(self):
            self.data = bytearray()

        def write(self, data):
            self.data.extend(data)

        def flush(self):
            pass

    class FakeHandler:
        headers = {}
        wfile = FakeWriter()

        def __init__(self):
            self.status = None
            self.response_headers = {}

        def send_response(self, status):
            self.status = status

        def send_header(self, key, value):
            self.response_headers[key] = value

        def end_headers(self):
            pass

    monkeypatch.setattr(server, "NOTIFIER", FakeNotifier())
    handler = FakeHandler()
    server.HsxHandler._stream_notifications(handler, {"after": ["7"]})

    assert handler.status == 200
    assert handler.response_headers["X-Accel-Buffering"] == "no"
    assert handler.response_headers["Cache-Control"] == "no-cache"
    assert seen_after[0][0] == 7
    assert b"id: 8\nevent: notification\ndata: " in handler.wfile.data


def test_web_and_tui_load_the_same_design_and_api_contract():
    web_tokens = json.loads((BASE_DIR / "web" / "design-tokens.json").read_text(encoding="utf-8"))
    web_api = json.loads((BASE_DIR / "web" / "api-contract.json").read_text(encoding="utf-8"))
    from agent import tui

    assert tui._TOKENS == web_tokens
    assert tui._API == web_api
    assert web_api["endpoints"]["chat_stream"] == "/api/chat/stream"
    assert web_api["endpoints"]["feedback"] == "/api/feedback"
    assert web_api["endpoints"]["telemetry"] == "/api/telemetry"
    assert web_api["endpoints"]["activity"] == "/api/activity"
    assert web_api["endpoints"]["notifications"] == "/api/notifications"
    assert web_api["endpoints"]["notifications_read"] == "/api/notifications/read"


def test_web_shell_has_shared_chat_hierarchy_and_feedback_widgets():
    html = (BASE_DIR / "web" / "index.html").read_text(encoding="utf-8")
    css = (BASE_DIR / "web" / "chat.css").read_text(encoding="utf-8")
    script = (BASE_DIR / "web" / "chat.js").read_text(encoding="utf-8")

    assert 'id="connections-title"' in html
    assert 'id="chat-stream"' in html
    assert 'id="conversation-layout"' in html
    assert 'id="greeting-title"' in html
    assert 'placeholder="Type / for skills"' in html
    assert 'id="voice-button"' in html
    assert 'id="composer-options-button"' in html
    assert 'id="voice-indicator"' in html
    assert 'class="message-author"' not in html
    assert 'id="composer-form"' in html
    assert 'id="live-camera"' in html
    assert 'id="sidebar-toggle"' in html
    assert 'id="camera-toggle"' in html
    assert 'id="panel-backdrop"' in html
    assert 'id="artifacts-list"' in html
    assert 'id="agent-activity-list"' in html
    assert 'id="camera-retry"' in html
    assert 'id="notifications-toggle"' in html
    assert 'id="notifications-panel"' in html
    assert 'id="activity-toggle"' in html
    assert 'id="notification-toast"' in html
    assert 'id="camera-clock"' in html
    assert 'id="camera-latency"' in html
    assert 'id="camera-clip-time"' in html
    assert 'id="file-input"' in html
    assert 'id="file-input" class="sr-only" type="file" multiple>' in html
    assert 'class="chat-header"' not in html
    assert 'class="camera-info"' not in html
    assert 'class="camera-caption"' not in html
    assert 'id="browse-files-button"' not in html
    assert html.index('id="new-chat-button"') < html.index('class="activity-panel sidebar-activity"') < html.index('class="connections"')
    assert html.count('id="agent-activity-list"') == 1
    assert 'data-feedback="good"' in html
    assert 'data-feedback="bad"' in html
    assert 'data-action="speak"' in html
    assert 'data-action="retry"' in html
    assert 'data-action="edit"' in html
    assert 'class="message-time"' in html
    assert "marked.parse" in script
    assert "function updateGreeting()" in script
    assert "function setConversationEmpty(isEmpty)" in script
    assert "function setupVoiceInput()" in script
    assert "window.webkitSpeechRecognition" in script
    assert "Authorization" in script
    assert "login-panel" not in html
    assert "bearerToken" in script
    assert "telemetry.provider" in script
    assert "hsx-bearer-token" in script
    assert "elements.composer.addEventListener('submit'" in script
    assert "elements.stop.addEventListener('click', stopTask)" in script
    assert "new AbortController()" in script
    assert "controller.abort()" in script
    assert "receivedDone" in script
    assert "The connection ended before Hsx finished. Your draft was kept." in script
    assert "submitFeedback(article, button.dataset.feedback)" in script
    assert "apiContract.endpoints.chat_stream" in script
    assert "apiContract.endpoints.feedback" in script
    assert "apiContract.endpoints.telemetry" in script
    assert "visibilitychange" in script
    assert "scheduleCameraReconnect" in script
    assert "apiContract.endpoints.outputs" in script
    assert "apiContract.endpoints.activity" in script
    assert "function retryCamera" in script
    assert "target_device: 'local_host'" in script
    assert "apiContract.endpoints.notifications" in script
    assert "apiContract.endpoints.notifications_read" in script
    assert "apiContract.endpoints.notifications_stream" in script
    assert "apiContract.endpoints.greeting" in script
    assert "startNotificationStream()" in script
    assert "speechSynthesis.speak" in script
    assert "elements.activityList.hidden = expanded" in script
    assert "function openMobilePanel" in script
    assert "X-Hsx-Captured-At" in script
    assert "WORKSPACE</span>" not in html
    assert "var(--background)" in css
    assert "grid-template-columns: 258px minmax(0, 1fr) 340px" in css
    assert "grid-template-columns: minmax(0, 1fr)" in css
    assert ".sidebar.is-open" in css
    assert ".camera-sidebar.is-open" in css
    assert ".artifacts-panel" in css
    assert ".activity-panel" in css
    assert "grid-template-rows: minmax(0, 1fr) auto" in css
    assert "aspect-ratio: 16 / 9" in css
    assert ".conversation-layout.is-empty" in css
    assert ".message-card[data-role=\"user\"]" in css
    assert ".message-card { max-width: 790px; margin: 0 auto 25px; padding: 0" in css
    assert html.index('class="sidebar-bottom"') < html.index('class="connections"')

    service_worker = (BASE_DIR / "web" / "service-worker.js").read_text(encoding="utf-8")
    assert "'/web/chat.css'" in service_worker
    assert "'/web/chat.js'" in service_worker
    legacy_script = (BASE_DIR / "web" / "app.js").read_text(encoding="utf-8")
    assert "authToken" not in legacy_script
    assert "showLogin" not in legacy_script


def test_tui_has_native_feedback_buttons_with_shared_layout():
    async def verify():
        app = HsxTUI("http://127.0.0.1:1")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.refresh_telemetry()
            assert "Could not reach HSX server" in app.query_one("#connection").render().plain
            app._new_message("assistant", "Hello **there**.", "test-job")
            await pilot.pause()
            assert app.query_one("#status-header")
            assert app.query_one("#message-stream")
            assert app.query_one("#composer-dock")
            assert app.query_one("#good-" + app.query_one(".message-card")._counter)
            assert app.query_one("#bad-" + app.query_one(".message-card")._counter)

    asyncio.run(verify())


def test_tui_activity_panel_uses_live_job_and_output_endpoints():
    async def verify():
        app = HsxTUI("http://127.0.0.1:1")
        requests = []

        def fake_api_request(endpoint, _payload=None):
            requests.append(endpoint)
            if endpoint.startswith("/api/activity"):
                return {"tasks": [{"state": "running", "summary": "Build a weekly report"}]}
            if endpoint == "/api/outputs":
                return {"files": [{"name": "report.pdf"}]}
            return {"provider": "not connected"}

        app._api_request = fake_api_request
        async with app.run_test(size=(100, 30)):
            await app.refresh_workspace_activity()
            label = app.query_one("#workspace-activity").render().plain
            assert "running — Build a weekly report" in label
            assert "Files: report.pdf" in label
            assert "/api/activity?session_id=terminal" in requests
            assert "/api/outputs" in requests

    asyncio.run(verify())


def test_tui_composer_stop_and_feedback_buttons_are_wired():
    async def verify():
        app = HsxTUI("http://127.0.0.1:1")
        api_calls = []
        prompts = []

        def fake_api_request(endpoint, payload=None):
            api_calls.append((endpoint, payload))
            return {"success": True}

        async def fake_submit_prompt(prompt):
            prompts.append(prompt)

        app._api_request = fake_api_request
        app._submit_prompt = fake_submit_prompt
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#prompt-input").value = "test prompt"
            await pilot.click("#send-button")
            await pilot.pause()
            assert prompts == ["test prompt"]

            app._new_message("assistant", "Done.", "feedback-job")
            await pilot.pause()
            feedback_id = app.query_one(".message-card")._counter
            await pilot.click(f"#good-{feedback_id}")
            await pilot.pause()
            assert api_calls[-1] == (
                "/api/feedback",
                {"job_id": "feedback-job", "feedback": "good"},
            )
            assert "Thanks!" in app.query_one(".feedback-status").render().plain

            await pilot.click("#stop-button")
            await pilot.pause()
            assert api_calls[-1] == ("/api/stop", {})

    asyncio.run(verify())


def test_tui_api_requests_do_not_send_authentication_headers(monkeypatch):
    seen_requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return b"{}"

    def fake_urlopen(request, timeout):
        seen_requests.append(request)
        return Response()

    monkeypatch.setattr("agent.tui.urllib.request.urlopen", fake_urlopen)
    HsxTUI("http://127.0.0.1:8000")._api_request("/api/telemetry")

    assert seen_requests[0].get_header("Authorization") is None


def test_feedback_and_telemetry_api_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ASK_JOBS", AskUserJobs(tmp_path / "jobs.db"))
    monkeypatch.setattr(server, "LLM_TELEMETRY", type("Telemetry", (), {
        "snapshot": lambda self: {"provider": "groq", "router_confidence": 0.9, "latency_ms": 12.0},
    })())
    feedback = []
    provider_feedback = []
    monkeypatch.setattr(server, "ROUTER_LOGGER", type("Logger", (), {
        "log_feedback": lambda self, session, job, rating: feedback.append((session, job, rating)),
    })())
    monkeypatch.setattr(
        server,
        "record_provider_feedback",
        lambda job_id, rating: provider_feedback.append((job_id, rating)),
    )
    job = server.ASK_JOBS.create_running("Prompt", "shared-session")

    status, _, body = _request("/api/telemetry")
    assert status == 200
    assert json.loads(body)["provider"] == "groq"
    assert "provider_stats" in json.loads(body)

    status, _, body = _request(
        "/api/feedback",
        method="POST",
        payload={"job_id": job["id"], "feedback": "good"},
    )
    assert status == 201
    assert json.loads(body)["success"] is True
    assert feedback == [("shared-session", job["id"], "good")]
    assert provider_feedback == [(job["id"], "good")]

    stopped = []
    monkeypatch.setattr(server.POLICY, "stop_all", lambda: stopped.append(True))
    status, _, body = _request("/api/stop", method="POST", payload={})
    assert status == 200
    assert json.loads(body) == {"success": True, "stopped": True}
    assert stopped == [True]
