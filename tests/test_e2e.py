import json
from pathlib import Path

import server
from agent.ask_user import AskUserJobs
from agent.workspace import Workspace
from tests.test_files_panel import _request


def test_authenticated_stream_creates_workspace_artifact(tmp_path: Path, monkeypatch):
    root = tmp_path / "share"
    root.mkdir()
    monkeypatch.setattr(server, "WORKSPACE", Workspace(root, "Hsx Work"))
    monkeypatch.setattr(server, "WORK_DIR", root / "Hsx Work")
    monkeypatch.setattr(server, "ASK_JOBS", AskUserJobs(tmp_path / "jobs.db"))
    monkeypatch.setattr(server.POLICY, "audit_event", lambda *args: None)

    def fake_run_chat_job(job_id, approvals, sensitive, target_device, on_text_delta):
        del approvals, sensitive, target_device
        artifact = server.WORKSPACE.work_dir / "answer.txt"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("e2e answer", encoding="utf-8")
        on_text_delta("e2e answer")
        completed = server.ASK_JOBS.complete(job_id, "e2e answer", [])
        server._emit_chat_event(job_id, "job", server._ask_job_payload(completed))
        server._emit_chat_event(job_id, "done", {})

    monkeypatch.setattr(server, "_run_chat_job", fake_run_chat_job)
    status, headers, body = _request(
        "/api/chat/stream",
        method="POST",
        payload={"message": "create an answer", "target_device": "client_browser"},
        stop_event="done",
    )
    assert status == 200
    assert headers["Content-Type"] == "text/event-stream"
    assert b"e2e answer" in body
    assert (root / "Hsx Work" / "answer.txt").read_text(encoding="utf-8") == "e2e answer"


def test_health_allows_requests_without_token():
    status, _, body = _request("/health", tokenless=True)
    assert status == 200
    assert body == b'{"status": "ok"}'


def test_stop_endpoint_cancels_running_chat_and_closes_its_stream(tmp_path: Path, monkeypatch):
    jobs = AskUserJobs(tmp_path / "jobs.db")
    job = jobs.create_running("Long task", "session")
    emitted = []
    stopped = []

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

    status, _, body = _request("/api/stop", method="POST", payload={})

    assert status == 200
    assert json.loads(body)["cancelled_jobs"] == 1
    assert stopped == [True]
    assert jobs.get(job["id"])["state"] == "cancelled"
    events = [item for item in emitted if len(item) == 3]
    assert [item[1] for item in events] == ["job", "done"]
    assert events[0][2]["id"] == job["id"]
    assert events[0][2]["state"] == "cancelled"


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


def test_doctor_report_does_not_print_key_values(monkeypatch, capsys):
    from doctor import _key_checks, print_report

    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-test-key")
    print_report(_key_checks())
    assert "super-secret-test-key" not in capsys.readouterr().out
