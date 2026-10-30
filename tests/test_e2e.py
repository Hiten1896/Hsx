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
    monkeypatch.setattr(server, "AUTH_TOKEN", "e2e-token")
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
        headers={"Authorization": "Bearer e2e-token"},
        payload={"message": "create an answer", "target_device": "client_browser"},
        stop_event="done",
    )
    assert status == 200
    assert headers["Content-Type"] == "text/event-stream"
    assert b"e2e answer" in body
    assert (root / "Hsx Work" / "answer.txt").read_text(encoding="utf-8") == "e2e answer"


def test_authentication_rejects_missing_token(monkeypatch):
    monkeypatch.setattr(server, "AUTH_TOKEN", "e2e-token")
    status, _, body = _request("/health")
    assert status == 401
    assert b"Authentication required" in body


def test_doctor_report_does_not_print_key_values(monkeypatch, capsys):
    from doctor import _key_checks, print_report

    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-test-key")
    print_report(_key_checks())
    assert "super-secret-test-key" not in capsys.readouterr().out
