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


def test_greeting_api_returns_the_single_generated_line(monkeypatch):
    monkeypatch.setattr(server, "_generate_greeting", lambda time_of_day: f"{time_of_day.title()} finds you well, Sir.")
    status, _, body = _request(
        "/api/greeting", method="POST", payload={"time_of_day": "afternoon"}
    )
    assert status == 200
    assert json.loads(body) == {"greeting": "Afternoon finds you well, Sir."}


def test_cli_displays_the_server_generated_greeting(monkeypatch, capsys):
    import cli

    monkeypatch.setattr(cli, "_request", lambda path, payload: {"greeting": "Good afternoon, Sir."})
    cli._show_greeting()
    assert capsys.readouterr().out.strip() == "HSX > Good afternoon, Sir."


def test_generated_artifact_filter_excludes_current_and_legacy_upload_paths():
    assert server._is_user_upload_path("Hsx Work/Uploads/screenshot.png")
    assert server._is_user_upload_path("Hsx Work/2025-01-03 1215 Uploads/screenshot.png")
    assert not server._is_user_upload_path("Hsx Work/2025-01-03 1215 Report/report.pdf")


def test_resource_alerts_include_disk_and_memory_cooldowns(monkeypatch):
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
    high = server.SystemMetrics(cpu_percent=99, ram_percent=95, temperature_c=None, outbound_connections=0)
    normal = server.SystemMetrics(cpu_percent=10, ram_percent=20, temperature_c=None, outbound_connections=0)
    server._notify_resource_alerts(high)
    server._notify_resource_alerts(high)
    assert len(sent) == 4
    assert {event for event, _, _ in sent} == {"disk_full", "low_memory"}
    assert all(kwargs["cooldown_seconds"] > 0 and kwargs["dedupe_key"] for _, _, kwargs in sent)

    disk_used[0] = 50
    server._notify_resource_alerts(normal)
    disk_used[0] = 90
    server._notify_resource_alerts(high)
    assert len(sent) == 6
    assert {event for event, _, _ in sent} == {"disk_full", "low_memory"}


def test_notification_sse_disables_buffering_and_resumes_from_requested_id(monkeypatch):
    seen_after = []

    class FakeNotifier:
        def get_broadcast_events_after(self, _last_id):
            return []

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

        def _security_headers(self):
            server.HsxHandler._security_headers(self)

        def _cors_headers(self):
            server.HsxHandler._cors_headers(self)

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
    assert "connect-src 'self'" in handler.response_headers["Content-Security-Policy"]
    assert seen_after[0][0] == 7
    assert b"id: 8\nevent: notification\ndata: " in handler.wfile.data


def test_web_and_tui_load_the_same_design_and_api_contract():
    web_tokens = json.loads((BASE_DIR / "web" / "design-tokens.json").read_text(encoding="utf-8"))
    web_api = json.loads((BASE_DIR / "web" / "api-contract.json").read_text(encoding="utf-8"))
    from agent import tui

    assert tui._TOKENS == web_tokens
    assert tui._API == web_api
    assert web_tokens["colors"]["background"] == "#1e1e1e"
    assert web_tokens["colors"]["accent"] == "#d2d2d2"
    assert "blue" not in web_tokens["colors"]
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
    assert '<h1 id="greeting-title" aria-busy="false">Good evening, Sir</h1>' in html
    assert 'placeholder="Type / for skills"' in html
    assert 'id="voice-button"' in html
    assert 'id="composer-camera-button"' not in html
    assert 'id="camera-toggle"' in html
    assert 'Enter to send' not in html
    assert 'class="message-attachments"' in html
    assert 'id="composer-options-button"' not in html
    assert 'id="voice-indicator"' in html
    assert 'id="composer-options-menu"' not in html
    assert 'class="message-author"' not in html
    assert 'id="composer-form"' in html
    assert '<p class="privacy-note">Made by HS</p>' in html
    assert '<span id="composer-status" role="status"></span>' in html
    assert 'Files up to 1 MB each' not in html
    assert 'id="live-camera"' in html
    assert 'id="sidebar-toggle"' in html
    assert 'id="camera-toggle"' in html
    assert 'id="panel-backdrop"' in html
    assert 'id="artifacts-list"' in html
    assert 'id="agent-activity-strip"' in html
    assert 'id="camera-retry"' in html
    assert 'class="panel-refresh camera-retry"' in html
    assert 'class="panel-refresh artifacts-refresh"' in html
    assert 'id="notifications-toggle"' in html
    assert 'id="notifications-panel"' in html
    assert 'id="activity-toggle"' not in html
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
    assert html.index('id="new-chat-button"') < html.index('id="agent-activity-strip"') < html.index('class="history-panel"')
    assert html.count('id="agent-activity-strip"') == 1
    assert 'data-feedback="good"' in html
    assert 'data-feedback="bad"' in html
    assert 'data-action="speak"' in html
    assert 'data-action="retry"' in html
    assert 'data-action="edit"' in html
    assert 'class="message-time"' in html
    assert "marked.parse" in script
    assert "function updateGreeting()" not in script
    assert "Good morning" not in script
    assert script.count("refreshGreeting();") == 2
    assert "function setConversationEmpty(isEmpty)" in script
    assert "function setupVoiceInput()" in script
    assert "window.webkitSpeechRecognition" in script
    assert "login-panel" not in html
    assert "bearerToken" not in script
    assert "telemetry.provider" in script
    assert "hsx-bearer-token" not in script
    assert "access-token-input" not in html
    assert "elements.composer.addEventListener('submit'" in script
    assert "elements.stop.addEventListener('click', stopTask)" in script
    assert "new AbortController()" in script
    assert "controller.abort()" in script
    assert "receivedDone" in script
    assert "The connection ended before Hsx finished. Your draft was kept." in script
    assert "void submitFeedback(article, button.dataset.feedback)" in script
    assert 'id="feedback-dialog"' not in html
    assert 'id="settings-panel-privacy"' in html
    assert 'id="save-feedback-setting"' in html
    assert 'id="full-conversation-setting"' in html
    assert 'id="file-contents-setting"' in html
    assert 'id="delete-feedback-button"' in html
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
    assert "function refreshGreeting()" in script
    assert "startNotificationStream()" in script
    assert "speechSynthesis.speak" in script
    assert "elements.activityList.hidden = false" in script
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
    assert html.index('class="history-panel"') < html.index('class="sidebar-bottom"')

    service_worker = (BASE_DIR / "web" / "service-worker.js").read_text(encoding="utf-8")
    assert "'/web/chat.css?v=16'" in service_worker
    assert "'/web/chat.js'" in service_worker
    legacy_script = (BASE_DIR / "web" / "app.js").read_text(encoding="utf-8")
    assert "authToken" not in legacy_script
    assert "showLogin" not in legacy_script


def test_web_user_actions_accordion_state_and_camera_stream_are_wired():
    html = (BASE_DIR / "web" / "index.html").read_text(encoding="utf-8")
    css = (BASE_DIR / "web" / "chat.css").read_text(encoding="utf-8")
    script = (BASE_DIR / "web" / "chat.js").read_text(encoding="utf-8")

    assert ".message-card[data-role=\"user\"] .message-content" in css
    assert ".message-card[data-role=\"user\"] .message-actions" in css
    assert 'id="sidebar-toggle"' in html
    assert "document.getElementById('sidebar-toggle').addEventListener('click'" in script
    assert ".panel-collapse-button[aria-expanded=\"false\"] svg { transform: none; }" in css
    assert 'aria-expanded="false"' in html
    assert 'access-token' not in html
    assert "hsx-api-token" not in script
    assert 'id="auth-dialog"' not in html
    assert "hsx-theme" in script
    assert "prefers-color-scheme: light" in script
    assert "Authorization" not in script
    assert 'id="emergency-banner"' in html
    assert "Emergency stop is active" in html
    assert "emergency_stop" in script
    assert "banner-resume-button" in script
    assert "telemetry.provider_status" in script
    assert "Fallback to Groq" in script or "providerCall.startsWith('Failed')" in script
    assert "Math.min(\n    frame.capturedAt * 1000 - cameraClockOffsetMs,\n    Date.now()," in script


def test_camera_is_lazy_and_run_state_resets_after_failure_or_stop():
    html = (BASE_DIR / "web" / "index.html").read_text(encoding="utf-8")
    css = (BASE_DIR / "web" / "chat.css").read_text(encoding="utf-8")
    script = (BASE_DIR / "web" / "chat.js").read_text(encoding="utf-8")

    assert 'id="camera-view" class="camera-view" role="button"' in html
    assert 'id="camera-status">Click to turn on camera</span>' in html
    assert ".camera-view { position: relative;" in css
    assert ".camera-badge-standby" in css
    assert "let cameraEnabled = false;" in script
    assert "function toggleCamera()" in script
    camera_toggle = script[script.index("async function toggleCamera()"):script.index("function findBytes(")]
    assert "cameraAvailable = true;" in camera_toggle
    assert "startCameraStream();" in camera_toggle
    assert "await refreshCamera();" not in camera_toggle
    assert "const MAX_CAMERA_LIVE_DELAY_MS = 1000;" in script
    camera_stream = script[script.index("async function startCameraStream()"):script.index("function updateCameraClock()")]
    assert "let newestFrame = null;" in camera_stream
    assert "frameAgeMs > MAX_CAMERA_LIVE_DELAY_MS" in camera_stream
    assert "scheduleCameraReconnect();" in camera_stream
    camera_refresh = script[script.index("async function refreshCamera()"):script.index("function handleCameraStateEvent")]
    assert "apiContract.endpoints.camera_status" in camera_refresh
    assert "apiContract.endpoints.camera_start" not in camera_refresh
    assert "window.setInterval(refreshCamera, 5000)" in script
    assert "elements.cameraView.addEventListener('click', toggleCamera)" in script
    assert "elements.cameraView.addEventListener('keydown'" in script
    assert "elements.send.disabled = isBusy;" not in script
    assert "elements.prompt.dispatchEvent(new Event('input', { bubbles: true }))" in script
    assert "elements.send.hidden = submitting" in script
    assert "composer-camera-button" not in script
    assert "if (activeChatController === controller)" in script
    stop_handler = script[script.index("async function stopTask()"):]
    assert "setBusy(false);" in stop_handler
    assert "const controller = new AbortController();\n      activeChatController = controller;\n      setBusy(true);" in script
    assert "async function pollQuestionJob(jobId, activity, signal)" in script
    assert "if (signal?.aborted) throw new DOMException('Task stopped.', 'AbortError');" in script
    assert "setCameraStatus(false, 'Camera feed offline');" in script
    assert ".sidebar { display: flex; flex-direction: column; min-width: 0; min-height: 0; overflow: hidden;" in css
    assert ".sidebar .connections { margin-top: auto; }" in css
    assert ".sidebar-activity .artifacts-list, .chat-history-list {" in css


def test_sidebar_accordions_and_settings_dialog_contract():
    html = (BASE_DIR / "web" / "index.html").read_text(encoding="utf-8")
    css = (BASE_DIR / "web" / "chat.css").read_text(encoding="utf-8")
    script = (BASE_DIR / "web" / "chat.js").read_text(encoding="utf-8")

    assert 'id="settings-toggle"' in html
    assert 'class="brand-inner"' in html
    assert 'class="workspace-chat-tile"' not in html
    assert 'id="agent-activity-strip" class="activity-running" aria-live="polite" hidden' in html
    assert 'id="chat-history-list" class="chat-history-list" aria-live="polite"' in html
    assert '<dialog id="settings-dialog"' in html
    assert 'role="tablist"' in html
    assert all(f'data-settings-tab="{tab}"' in html for tab in (
        "general", "voice", "models", "camera", "privacy", "advanced", "about"
    ))
    assert '<details class="feedback-settings">' not in html
    assert ".settings-dialog {" in css and "100dvh" in css
    assert "endpoints.provider_health" in script
    assert "settings-dialog" in script
    assert "chat-history-action" in script
    assert "window.confirm(`Delete “${session.title" in script
    assert "document.activeElement === first" in script
    assert "settingsReturnFocus.focus()" in script
    assert "prefers-reduced-motion: reduce" in css
    assert "overflow-x: hidden" in css
    assert "min-height: 40px" in css


def test_sidebar_defaults_and_live_suggestion_contract():
    html = (BASE_DIR / "web" / "index.html").read_text(encoding="utf-8")
    css = (BASE_DIR / "web" / "chat.css").read_text(encoding="utf-8")

    assert 'id="chat-history-list" class="chat-history-list" aria-live="polite"' in html
    assert 'id="activity-running-title" class="activity-running-title"' in html
    assert 'id="connection-toggle"' in html
    assert html.count('class="suggestion-chip"') == 4
    assert "min-height: 40px" in css
    assert "text-overflow: ellipsis" in css
    assert "overflow-x: hidden" in css


def test_tui_has_native_feedback_buttons_with_shared_layout():
    async def verify():
        app = HsxTUI("http://127.0.0.1:1")
        app._fetch_greeting = lambda: "Good afternoon, Sir."
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert "Good afternoon, Sir." in app.query_one("#welcome-card").render().plain
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
            return {"success": True, "saved": True}

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
                {"job_id": "feedback-job", "feedback": "good", "client": "cli"},
            )
            assert "Thanks!" in app.query_one(".feedback-status").render().plain
            await pilot.click(f"#bad-{feedback_id}")
            await pilot.pause()
            assert api_calls[-1][1]["feedback"] == "bad"

            await pilot.click("#stop-button")
            await pilot.pause()
            assert api_calls[-1] == ("/api/stop", {})

    asyncio.run(verify())


def test_tui_api_requests_do_not_send_bearer_auth(monkeypatch):
    seen_requests = []
    monkeypatch.setenv("HSX_REMOTE_TOKEN", "test-token")

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
    from agent.feedback import FeedbackStore

    feedback_store = FeedbackStore(tmp_path / "feedback.db")
    feedback_store.update_settings(save_feedback=True, include_full_conversation=False)
    monkeypatch.setattr(server, "FEEDBACK_STORE", feedback_store)
    monkeypatch.setattr(server, "LLM_TELEMETRY", type("Telemetry", (), {
        "snapshot": lambda self: {"provider": "groq", "router_confidence": 0.9, "latency_ms": 12.0},
    })())
    provenance = {
        "provider": "gemini",
        "model": "gemini-test",
        "provider_calls": [{"provider": "gemini", "model": "gemini-test"}],
        "tool_names": ["read_file"],
    }
    monkeypatch.setattr(server, "PROVIDER_BANDIT", type("ProviderBandit", (), {
        "job_provenance": lambda self, job_id: provenance,
        "snapshot": lambda self: {"providers": {}},
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
    server.ASK_JOBS.complete(job["id"], "Response", [])

    status, _, body = _request("/api/telemetry")
    assert status == 200
    assert json.loads(body)["provider"] == "groq"
    assert "provider_stats" in json.loads(body)

    status, _, body = _request(
        "/api/feedback",
        method="POST",
        payload={
            "job_id": job["id"],
            "feedback": "down",
            "client": "cli",
            "reason": "Not useful",
            "note": "Try a shorter response",
            "prompt_messages": [{"role": "user", "content": "client supplied wrong prompt"}],
            "response": "client supplied wrong response",
        },
    )
    assert status == 201
    assert json.loads(body)["success"] is True
    assert feedback == [("shared-session", job["id"], "bad")]
    assert provider_feedback == [(job["id"], "bad")]
    saved_feedback = feedback_store.export_records()[0]
    assert saved_feedback["rating"] == "down"
    assert saved_feedback["reason"] == "Not useful"
    assert saved_feedback["note"] == "Try a shorter response"
    assert saved_feedback["metadata"]["client"] == "cli"
    assert saved_feedback["metadata"]["provider"] == "gemini"
    assert saved_feedback["metadata"]["model"] == "gemini-test"
    assert saved_feedback["metadata"]["provider_calls"] == provenance["provider_calls"]
    assert saved_feedback["metadata"]["tool_names"] == ["read_file"]
    assert saved_feedback["prompt_messages"] == [{"role": "user", "content": "Prompt"}]
    assert saved_feedback["response"] == "Response"

    status, _, body = _request(
        "/api/feedback/settings",
        method="POST",
        payload={
            "save_feedback": False,
            "include_full_conversation": False,
            "include_file_contents": False,
        },
    )
    assert status == 200
    assert json.loads(body)["save_feedback"] is False
    status, _, body = _request(
        "/api/feedback",
        method="POST",
        payload={"job_id": job["id"], "feedback": "up"},
    )
    assert status == 201
    assert json.loads(body)["saved"] is False

    status, _, body = _request(
        "/api/feedback/delete",
        method="POST",
        payload={"confirm": True},
    )
    assert status == 200
    assert json.loads(body)["deleted"] == 1
    assert feedback_store.settings()["count"] == 0

    stopped = []
    monkeypatch.setattr(server.POLICY, "stop_all", lambda: stopped.append(True))
    status, _, body = _request("/api/stop", method="POST", payload={})
    assert status == 200
    assert json.loads(body)["stopped"] is False
    assert stopped == []
