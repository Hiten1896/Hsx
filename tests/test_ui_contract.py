import asyncio
import json
from pathlib import Path

import server
from agent.ask_user import AskUserJobs
from agent.tui import HsxTUI
from tests.test_files_panel import _request

BASE_DIR = Path(__file__).resolve().parents[1]


def test_web_and_tui_load_the_same_design_and_api_contract():
    web_tokens = json.loads((BASE_DIR / "web" / "design-tokens.json").read_text(encoding="utf-8"))
    web_api = json.loads((BASE_DIR / "web" / "api-contract.json").read_text(encoding="utf-8"))
    from agent import tui

    assert tui._TOKENS == web_tokens
    assert tui._API == web_api
    assert web_api["endpoints"]["chat_stream"] == "/api/chat/stream"
    assert web_api["endpoints"]["feedback"] == "/api/feedback"
    assert web_api["endpoints"]["telemetry"] == "/api/telemetry"


def test_web_shell_has_shared_chat_hierarchy_and_feedback_widgets():
    html = (BASE_DIR / "web" / "index.html").read_text(encoding="utf-8")
    css = (BASE_DIR / "web" / "chat.css").read_text(encoding="utf-8")
    script = (BASE_DIR / "web" / "chat.js").read_text(encoding="utf-8")

    assert 'id="connections-title"' in html
    assert 'id="chat-stream"' in html
    assert 'id="composer-form"' in html
    assert 'id="live-camera"' in html
    assert 'id="sidebar-toggle"' in html
    assert 'id="camera-toggle"' in html
    assert 'id="panel-backdrop"' in html
    assert 'id="artifacts-list"' in html
    assert 'id="camera-clock"' in html
    assert 'id="camera-latency"' in html
    assert 'id="camera-clip-time"' in html
    assert 'id="file-input"' in html
    assert 'data-feedback="good"' in html
    assert 'data-feedback="bad"' in html
    assert "marked.parse" in script
    assert "Authorization" in script
    assert "login-panel" not in html
    assert "bearerToken" in script
    assert "telemetry.provider" in script
    assert "hsx-bearer-token" in script
    assert "elements.composer.addEventListener('submit'" in script
    assert "elements.stop.addEventListener('click', stopTask)" in script
    assert "submitFeedback(article, button.dataset.feedback)" in script
    assert "apiContract.endpoints.chat_stream" in script
    assert "apiContract.endpoints.feedback" in script
    assert "apiContract.endpoints.telemetry" in script
    assert "visibilitychange" in script
    assert "scheduleCameraReconnect" in script
    assert "apiContract.endpoints.outputs" in script
    assert "function openMobilePanel" in script
    assert "X-Hsx-Captured-At" in script
    assert "WORKSPACE</span>" not in html
    assert "var(--background)" in css
    assert "grid-template-columns: 258px minmax(0, 1fr) 340px" in css
    assert "grid-template-columns: minmax(0, 1fr)" in css
    assert ".sidebar.is-open" in css
    assert ".camera-sidebar.is-open" in css
    assert ".artifacts-panel" in css

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
