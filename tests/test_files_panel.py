import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import server
from agent.ask_user import AskUserJobs, AskUserRequest
from agent.workspace import Workspace


def _request(
    path: str,
    method: str = "GET",
    headers: dict | None = None,
    payload: dict | None = None,
    stop_event: str | None = None,
):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.HsxHandler)
    thread = threading.Thread(target=httpd.handle_request)
    thread.start()
    request_headers = headers or {}
    data = None
    if payload is not None:
        request_headers = {**request_headers, "Content-Type": "application/json"}
        data = json.dumps(payload).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{httpd.server_port}{path}",
        data=data,
        method=method,
        headers=request_headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            if stop_event:
                body = []
                while line := response.readline():
                    body.append(line)
                    if line.startswith(f"event: {stop_event}".encode()):
                        break
                result = response.status, response.headers, b"".join(body)
            else:
                result = response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        result = error.code, error.headers, error.read()
    thread.join(timeout=2)
    httpd.server_close()
    return result


def test_files_panel_lists_workspace_files_and_serves_ranges(tmp_path: Path, monkeypatch):
    root = tmp_path / "share"
    (root / "Hsx Work").mkdir(parents=True)
    (root / "report.txt").write_text("workspace text", encoding="utf-8")
    (root / "Hsx Work" / "result.pdf").write_bytes(b"0123456789")
    monkeypatch.setattr(server, "WORKSPACE", Workspace(root, "Hsx Work"))
    monkeypatch.setattr(server, "AUTH_TOKEN", "")

    status, _, body = _request("/api/files")
    assert status == 200
    assert {item["name"] for item in json.loads(body)["files"]} >= {"report.txt", "result.pdf"}

    status, headers, body = _request("/api/files/content?path=Hsx%20Work%2Fresult.pdf")
    assert status == 200
    assert headers["Content-Type"] == "application/pdf"
    assert body == b"0123456789"

    status, headers, body = _request(
        "/api/files/content?path=Hsx%20Work%2Fresult.pdf",
        headers={"Range": "bytes=2-5"},
    )
    assert status == 206
    assert headers["Content-Range"] == "bytes 2-5/10"
    assert body == b"2345"


def test_chat_stream_forwards_model_text_deltas(tmp_path: Path, monkeypatch):
    root = tmp_path / "share"
    root.mkdir()
    monkeypatch.setattr(server, "WORKSPACE", Workspace(root, "Hsx Work"))
    monkeypatch.setattr(server, "WORK_DIR", root / "Hsx Work")
    monkeypatch.setattr(server, "ASK_JOBS", AskUserJobs(tmp_path / "jobs.db"))
    monkeypatch.setattr(server, "AUTH_TOKEN", "")
    monkeypatch.setattr(server.POLICY, "audit_event", lambda *args: None)

    def fake_run_chat_job(job_id, approvals, sensitive, target_device, on_text_delta):
        on_text_delta("Streaming ")
        on_text_delta("answer")
        completed = server.ASK_JOBS.complete(job_id, "Streaming answer", [])
        server._emit_chat_event(job_id, "job", server._ask_job_payload(completed))
        server._emit_chat_event(job_id, "done", {})

    monkeypatch.setattr(server, "_run_chat_job", fake_run_chat_job)
    status, headers, body = _request(
        "/api/chat/stream",
        method="POST",
        payload={"message": "Say hello", "target_device": "client_browser"},
        stop_event="done",
    )

    text = body.decode()
    assert status == 200
    assert headers["Content-Type"] == "text/event-stream"
    assert "event: acknowledgement" in text
    assert "event: text_delta\ndata: {\"text\":\"Streaming \"}" in text
    assert "event: text_delta\ndata: {\"text\":\"answer\"}" in text
    assert '"result":"Streaming answer"' in text


def test_files_panel_rejects_traversal(tmp_path: Path, monkeypatch):
    root = tmp_path / "share"
    (root / "Hsx Work").mkdir(parents=True)
    (tmp_path / "secret.txt").write_text("not exposed", encoding="utf-8")
    monkeypatch.setattr(server, "WORKSPACE", Workspace(root, "Hsx Work"))
    monkeypatch.setattr(server, "AUTH_TOKEN", "")

    status, _, _ = _request("/api/files/content?path=..%2Fsecret.txt")
    assert status == 404


def test_files_panel_requires_bearer_auth(monkeypatch):
    monkeypatch.setattr(server, "AUTH_TOKEN", "test-token")
    status, _, body = _request("/api/files")
    assert status == 401
    assert b"Authentication" in body


def test_chat_question_card_answer_resumes_job(tmp_path: Path, monkeypatch):
    root = tmp_path / "share"
    work_dir = root / "Hsx Work"
    work_dir.mkdir(parents=True)
    workspace = Workspace(root, "Hsx Work")
    jobs = AskUserJobs(tmp_path / "jobs.db")
    notifications = []

    class NotifierStub:
        def notify(self, event, title, **kwargs):
            notifications.append((event, title, kwargs))

    calls = []

    def run_agent(request, history, tools, skill_map, **kwargs):
        calls.append(kwargs.get("resume_tool_result"))
        if kwargs.get("resume_tool_result") is None:
            question = AskUserRequest("Which file is the template?", ["layout.docx"])
            question.checkpoint = [{"role": "user", "content": request}]
            question.tool_call_id = "ask-call-1"
            raise question
        assert kwargs["resume_tool_result"] == {
            "tool_call_id": "ask-call-1",
            "answer": "layout.docx",
        }
        return "The report is ready.", history + [{"role": "assistant", "content": "The report is ready."}]

    monkeypatch.setattr(server, "WORKSPACE", workspace)
    monkeypatch.setattr(server, "WORKSPACE_DIR", root)
    monkeypatch.setattr(server, "WORK_DIR", work_dir)
    monkeypatch.setattr(server, "ASK_JOBS", jobs)
    monkeypatch.setattr(server, "NOTIFIER", NotifierStub())
    monkeypatch.setattr(server, "AUTH_TOKEN", "")
    monkeypatch.setattr(server, "run_agent", run_agent)
    monkeypatch.setattr(server.POLICY, "audit_event", lambda *args: None)
    monkeypatch.setattr(server, "SESSION_HISTORIES", {})
    monkeypatch.setattr(server, "SESSION_LOCKS", {})

    status, _, body = _request(
        "/api/chat",
        method="POST",
        payload={"message": "mak a repot frm file1 frmt n file2 data pdf", "session_id": "phase18-test"},
    )
    response = json.loads(body)
    assert status == 202
    assert response["response"].startswith("Understood:")
    job_id = response["job"]["id"]

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and jobs.get(job_id)["state"] == "running":
        time.sleep(0.01)
    waiting = jobs.get(job_id)
    assert waiting["state"] == "waiting_for_user"
    assert waiting["question"] == "Which file is the template?"

    status, _, body = _request(
        f"/api/ask-jobs/{job_id}/answer",
        method="POST",
        payload={"answer": "layout.docx"},
    )
    assert status == 202

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and jobs.get(job_id)["state"] == "running":
        time.sleep(0.01)
    complete = jobs.get(job_id)
    assert complete["state"] == "done"
    assert complete["result"] == "The report is ready."
    assert calls == [None, {"tool_call_id": "ask-call-1", "answer": "layout.docx"}]
    assert notifications[0][0:2] == ("needs_input", "Hsx needs your input")
    assert notifications[-1][0:2] == ("job_done", "Your Hsx task is complete")
