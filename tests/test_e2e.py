import json
import io
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import server
from agent import llm_client
from agent.ask_user import AskUserJobs
from agent.llm.providers import GeminiProvider, OpenAICompatibleProvider, assert_payload_valid
from agent.llm.router import LLMRouter
from agent.workspace import Workspace
from tests.test_files_panel import _request


@pytest.mark.parametrize("provider_kind", ["gemini", "groq"])
def test_web_chat_stream_with_strict_fake_provider(provider_kind, tmp_path, monkeypatch):
    class StrictProviderHandler(BaseHTTPRequestHandler):
        requests = []

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert_payload_valid(provider_kind, payload)
            type(self).requests.append(payload)
            if len(type(self).requests) == 1:
                if provider_kind == "gemini":
                    response = {"candidates": [{"content": {"parts": [{
                        "functionCall": {
                            "name": "echo",
                            "args": {"value": "from tool"},
                            "id": "strict-call",
                        },
                        "thoughtSignature": "required-signature",
                    }]}}]}
                else:
                    response = {"choices": [{"message": {
                        "content": None,
                        "tool_calls": [{
                            "id": "strict-call",
                            "type": "function",
                            "function": {"name": "echo", "arguments": '{"value":"from tool"}'},
                        }],
                    }, "finish_reason": "tool_calls"}]}
            else:
                if provider_kind == "gemini":
                    history_parts = [
                        part
                        for content in payload["contents"]
                        for part in content["parts"]
                    ]
                    calls = [part for part in history_parts if "functionCall" in part]
                    assert calls == [{
                        "functionCall": {
                            "name": "echo",
                            "args": {"value": "from tool"},
                            "id": "strict-call",
                        },
                        "thoughtSignature": "required-signature",
                    }]
                    assert any(
                        part.get("functionResponse", {}).get("id") == "strict-call"
                        and part["functionResponse"]["response"] == {"echo": "from tool"}
                        for part in history_parts
                    )
                    response = {"candidates": [{"content": {"parts": [{"text": "The tool returned from tool."}]}}]}
                else:
                    messages = payload["messages"]
                    assert any(
                        item.get("role") == "tool"
                        and item.get("tool_call_id") == "strict-call"
                        and item.get("content") == '{"echo": "from tool"}'
                        for item in messages
                    )
                    response = {"choices": [{"message": {
                        "content": "The tool returned from tool.",
                    }, "finish_reason": "stop"}]}
            encoded = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_args):
            pass

    fake_server = ThreadingHTTPServer(("127.0.0.1", 0), StrictProviderHandler)
    thread = threading.Thread(target=fake_server.serve_forever, daemon=True)
    thread.start()
    root = tmp_path / "share"
    root.mkdir()
    monkeypatch.setattr(server, "WORKSPACE", Workspace(root, "Hsx Work"))
    monkeypatch.setattr(server, "WORK_DIR", root / "Hsx Work")
    monkeypatch.setattr(server, "ASK_JOBS", AskUserJobs(tmp_path / "jobs.db"))
    monkeypatch.setattr(server.POLICY, "audit_event", lambda *args: None)
    monkeypatch.setattr(server.ROUTER_LOGGER, "log_prompt", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        server,
        "TOOLS",
        [{"name": "echo", "description": "Echo a value.", "input_schema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        }}],
    )
    monkeypatch.setattr(server, "SKILL_MAP", {"echo": lambda value: {"echo": value}})
    if provider_kind == "gemini":
        provider = GeminiProvider("fake-key", f"http://127.0.0.1:{fake_server.server_port}")
    else:
        provider = OpenAICompatibleProvider(
            "groq", "fake-key", "fake-model", f"http://127.0.0.1:{fake_server.server_port}"
        )
    monkeypatch.setattr(llm_client, "default_router", lambda: LLMRouter([provider]))

    try:
        status, _, body = _request(
            "/api/chat/stream",
            method="POST",
            payload={
                "message": "Use the echo tool with from tool.",
                "session_id": f"strict-{provider_kind}-{time.time_ns()}",
                "target_device": "client_browser",
                "sensitive": True,
            },
            stop_event="done",
        )
    finally:
        fake_server.shutdown()
        thread.join()

    stream = body.decode()
    assert status == 200
    assert "The tool returned from tool." in stream
    assert '"success": true' not in stream
    assert "event: tool_start" in stream
    assert "event: tool_end" in stream
    assert len(StrictProviderHandler.requests) == 2


def test_authenticated_stream_creates_workspace_artifact(tmp_path: Path, monkeypatch):
    root = tmp_path / "share"
    root.mkdir()
    monkeypatch.setattr(server, "WORKSPACE", Workspace(root, "Hsx Work"))
    monkeypatch.setattr(server, "WORK_DIR", root / "Hsx Work")
    monkeypatch.setattr(server, "ASK_JOBS", AskUserJobs(tmp_path / "jobs.db"))
    monkeypatch.setattr(server, "CHAT_STREAMS", server.ChatStreamRegistry())
    monkeypatch.setattr(server, "RUN_CANCEL_EVENTS", {})
    monkeypatch.setattr(server, "SESSION_LAST_JOBS", {})
    monkeypatch.setattr(server.POLICY, "audit_event", lambda *args: None)
    calls = []

    def fake_run_chat_job(
        job_id, approvals, sensitive, target_device, on_text_delta, cancel_event=None, on_provider_notice=None,
        on_activity_event=None,
    ):
        calls.append(job_id)
        del cancel_event
        del approvals, sensitive, target_device, on_activity_event
        artifact = server.WORKSPACE.work_dir / "answer.txt"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("e2e answer", encoding="utf-8")
        on_text_delta("e2e answer")
        completed = server.ASK_JOBS.complete(job_id, "e2e answer", [])
        server._emit_chat_event(job_id, "job", server._ask_job_payload(completed))
        server._emit_chat_event(job_id, "done", {})

    monkeypatch.setattr(server, "_run_chat_job", fake_run_chat_job)
    payload = {
        "message": "create an answer",
        "target_device": "client_browser",
        "session_id": "idempotent-session",
        "client_message_id": "answer-request-1",
    }
    status, headers, body = _request(
        "/api/chat/stream",
        method="POST",
        payload=payload,
        stop_event="done",
    )
    assert status == 200
    assert headers["Content-Type"] == "text/event-stream"
    assert b"e2e answer" in body
    status, _, resumed_body = _request(
        "/api/chat/stream",
        method="POST",
        headers={"Last-Event-ID": "2"},
        payload=payload,
        stop_event="done",
    )
    assert status == 200
    assert b"e2e answer" in resumed_body
    assert b"id: 3\n" in resumed_body
    assert calls == [server.ASK_JOBS.get_by_client_message(
        "idempotent-session",
        "answer-request-1",
    )["id"]]
    assert (root / "Hsx Work" / "answer.txt").read_text(encoding="utf-8") == "e2e answer"


def test_chat_stream_sends_done_and_closes_after_final_text_within_two_seconds(monkeypatch):
    monkeypatch.setattr(server.POLICY, "audit_event", lambda *args: None)

    def fake_run_chat_job(
        job_id, approvals, sensitive, target_device, on_text_delta, cancel_event=None, on_provider_notice=None,
        on_activity_event=None,
    ):
        del approvals, sensitive, target_device, cancel_event, on_activity_event
        on_text_delta("hello")
        completed = server.ASK_JOBS.get(job_id)
        if completed and completed["state"] == "running":
            server.ASK_JOBS.complete(job_id, "hello", [])
        server._emit_chat_event(job_id, "done", {})

    monkeypatch.setattr(server, "_run_chat_job", fake_run_chat_job)
    started = time.monotonic()
    status, _, body = _request(
        "/api/chat/stream", method="POST",
        payload={"message": "hello", "session_id": f"timing-{time.time_ns()}", "target_device": "client_browser"},
        stop_event="close",
    )
    assert status == 200
    assert b"event: text_delta" in body
    assert b"event: done" in body
    assert time.monotonic() - started < 2


def test_health_allows_requests_without_token():
    status, _, body = _request("/health", tokenless=True)
    assert status == 200
    assert body == b'{"status": "ok"}'


def test_stop_endpoint_cancels_running_chat_and_closes_its_stream(tmp_path: Path, monkeypatch):
    jobs = AskUserJobs(tmp_path / "jobs.db")
    job = jobs.create_running("Long task", "session")
    emitted = []
    stopped = []
    cancel_event = __import__("threading").Event()
    server.RUN_CANCEL_EVENTS[job["id"]] = cancel_event

    class FakePolicy:
        def stop_all(self):
            stopped.append(True)

        def audit_event(self, *args):
            emitted.append(("audit", *args))

    monkeypatch.setattr(server, "ASK_JOBS", jobs)
    monkeypatch.setattr(server, "POLICY", FakePolicy())
    monkeypatch.setattr(
        server,
        "_emit_chat_event",
        lambda job_id, event, payload: emitted.append((job_id, event, payload)),
    )

    status, _, body = _request("/api/stop", method="POST", payload={"run_id": job["id"]})

    assert status == 200
    assert json.loads(body)["stopped"] is True
    assert stopped == []
    assert cancel_event.is_set()
    assert jobs.get(job["id"])["state"] == "running"


def test_emergency_stop_is_separate_from_run_stop(monkeypatch):
    calls = []

    class FakePolicy:
        def stop_all(self):
            calls.append("stop")

        def reset_stop(self):
            calls.append("resume")

    monkeypatch.setattr(server, "POLICY", FakePolicy())

    def post(path):
        handler = server.HsxHandler.__new__(server.HsxHandler)
        handler.path = path
        handler.command = "POST"
        handler.client_address = ("127.0.0.1", 12345)
        handler.headers = {
            "Content-Length": "2",
            "Authorization": f"Bearer {server.AUTH_TOKEN}",
        }
        handler.rfile = io.BytesIO(b"{}")
        handler.wfile = io.BytesIO()
        handler.send_response = lambda status: setattr(handler, "response_status", status)
        handler.send_header = lambda *_args: None
        handler.end_headers = lambda: None
        handler.do_POST()
        return handler.response_status, json.loads(handler.wfile.getvalue())

    assert post("/api/emergency-stop")[0] == 200
    assert post("/api/resume")[0] == 200
    assert calls == ["stop", "resume"]


def test_healthz_reports_clock_and_api_security_headers():
    status, headers, body = _request("/healthz")
    assert status == 200
    assert "warnings" in json.loads(body)
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["Permissions-Policy"] == "camera=(self), microphone=(self)"
    assert "connect-src 'self'" in headers["Content-Security-Policy"]
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store"


def test_provider_health_probes_each_provider_without_tools(monkeypatch):
    from agent.llm.types import LLMEvent

    probed = []

    class FakeProvider:
        def __init__(self, name):
            self.name = name
            self.model = f"{name}-model"

        def generate(self, request):
            assert request.tools == []
            assert request.messages == [{"role": "user", "content": "Reply with OK."}]
            probed.append(self.name)
            yield LLMEvent(type="text_delta", text="OK")
            yield LLMEvent(type="stop")

    class FakeRouter:
        providers = [FakeProvider("gemini"), FakeProvider("groq")]

    monkeypatch.setattr(server, "default_router", lambda: FakeRouter())
    status, _, body = _request("/healthz/providers")
    assert status == 200
    assert probed == ["gemini", "groq"]
    assert json.loads(body) == {"providers": [
        {"provider": "gemini", "model": "gemini-model", "status": "ok"},
        {"provider": "groq", "model": "groq-model", "status": "ok"},
    ]}


def test_doctor_report_does_not_print_key_values(monkeypatch, capsys):
    from doctor import _key_checks, print_report

    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-test-key")
    print_report(_key_checks())
    assert "super-secret-test-key" not in capsys.readouterr().out
