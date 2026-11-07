import base64
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import server
from agent.ask_user import AskUserJobs, AskUserRequest
from agent.notifier import Notifier
from agent.workspace import Workspace


def _request(
    path: str,
    method: str = "GET",
    headers: dict | None = None,
    payload: dict | None = None,
    stop_event: str | None = None,
    tokenless: bool = False,
):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.HsxHandler)
    thread = threading.Thread(target=httpd.handle_request)
    thread.start()
    request_headers = dict(headers or {})
    token = getattr(server, "AUTH_TOKEN", "")
    if not tokenless and token:
        request_headers.setdefault("Authorization", f"Bearer {token}")
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
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
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
    finally:
        thread.join(timeout=15)
        httpd.server_close()
    return result


def test_files_panel_lists_workspace_files_and_serves_ranges(tmp_path: Path, monkeypatch):
    root = tmp_path / "share"
    (root / "Hsx Work").mkdir(parents=True)
    (root / "report.txt").write_text("workspace text", encoding="utf-8")
    (root / "Hsx Work" / "result.pdf").write_bytes(b"0123456789")
    monkeypatch.setattr(server, "WORKSPACE", Workspace(root, "Hsx Work"))

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


def test_outputs_endpoint_lists_generated_artifacts(tmp_path: Path, monkeypatch):
    root = tmp_path / "share"
    output_dir = root / "Hsx Work"
    output_dir.mkdir(parents=True)
    (output_dir / "report.pdf").write_bytes(b"pdf")
    (root / "source.txt").write_text("input", encoding="utf-8")
    monkeypatch.setattr(server, "WORKSPACE", Workspace(root, "Hsx Work"))
    monkeypatch.setattr(server, "WORKSPACE_DIR", root)
    monkeypatch.setattr(server, "WORK_DIR", output_dir)

    status, _, body = _request("/api/outputs")

    assert status == 200
    files = json.loads(body)["files"]
    assert [item["name"] for item in files] == ["report.pdf"]
    assert files[0]["path"] == "Hsx Work/report.pdf"
    payload = json.loads(body)
    assert payload["storage_available"] is True
    assert payload["storage_path"] == str(output_dir)


def test_outputs_panel_shows_shared_folder_files_across_chat_sessions(tmp_path: Path, monkeypatch):
    from agent.artifacts import ArtifactStore

    root = tmp_path / "network-share"
    output_dir = root / "Hsx Work"
    task_dir = output_dir / "another-device-task"
    task_dir.mkdir(parents=True)
    artifact_path = task_dir / "shared-report.pdf"
    artifact_path.write_bytes(b"pdf")
    uploads = output_dir / "Uploads"
    uploads.mkdir()
    (uploads / "user-photo.png").write_bytes(b"image")
    workspace = Workspace(root, "Hsx Work")
    artifact_store = ArtifactStore(tmp_path / "artifacts.sqlite")
    monkeypatch.setattr(server, "WORKSPACE", workspace)
    monkeypatch.setattr(server, "WORK_DIR", output_dir)
    monkeypatch.setattr(server, "ARTIFACTS", artifact_store)
    monkeypatch.setattr(server, "ASK_JOBS", None)

    first_listing = server._session_artifacts("")
    created = first_listing[0]["created"]
    second_listing = server._session_artifacts("")

    assert [item["name"] for item in first_listing] == ["shared-report.pdf"]
    assert first_listing[0]["session_id"] == ""
    assert second_listing[0]["created"] == created


def test_activity_endpoint_lists_only_the_session_and_hides_sensitive_prompts(tmp_path: Path, monkeypatch):
    jobs = AskUserJobs(tmp_path / "jobs.db")
    jobs.create_running("Create a weekly report", "browser-session")
    jobs.create_running("private API_KEY=do-not-show", "browser-session", sensitive=True)
    jobs.create_running("Other session task", "other-session")
    monkeypatch.setattr(server, "ASK_JOBS", jobs)

    status, _, body = _request("/api/activity?session_id=browser-session")

    assert status == 200
    tasks = json.loads(body)["tasks"]
    assert len(tasks) == 2
    assert {item["summary"] for item in tasks} == {"Create a weekly report", "Sensitive task"}
    assert all(item["state"] == "running" for item in tasks)


def test_session_artifacts_hide_internal_activity_trace(tmp_path, monkeypatch):
    root = tmp_path / "share"
    task_dir = root / "Hsx Work" / "make-report"
    task_dir.mkdir(parents=True)
    (task_dir / "report.pdf").write_bytes(b"pdf")
    (task_dir / "activity-trace.jsonl").write_text('{"event":"tool_start"}\n', encoding="utf-8")
    jobs = AskUserJobs(tmp_path / "jobs.db")
    jobs.create_running("Make a report PDF", "browser-session", "make-report")
    monkeypatch.setattr(server, "ASK_JOBS", jobs)
    monkeypatch.setattr(server, "WORKSPACE", Workspace(root, "Hsx Work"))
    monkeypatch.setattr(server, "WORK_DIR", root / "Hsx Work")

    files = server._session_artifacts("browser-session")

    assert [item["name"] for item in files] == ["report.pdf"]


def test_web_notifications_list_approval_and_mark_it_read(tmp_path: Path, monkeypatch):
    notifier = Notifier(tmp_path / "notifications.db")
    notifier.notify("needs_approval", "Hsx needs approval", job_id="approval-job")
    monkeypatch.setattr(server, "NOTIFIER", notifier)

    status, _, body = _request("/api/notifications")
    assert status == 200
    result = json.loads(body)
    assert result["unread"] == 1
    assert result["notifications"][0]["event"] == "needs_approval"
    assert result["notifications"][0]["job_id"] == "approval-job"

    status, _, body = _request(
        "/api/notifications/read",
        method="POST",
        payload={"id": result["notifications"][0]["id"]},
    )
    assert status == 200
    assert json.loads(body)["unread"] == 0


def test_text_upload_returns_workspace_path_for_chat_attachment(tmp_path: Path, monkeypatch):
    root = tmp_path / "share"
    root.mkdir()
    monkeypatch.setattr(server, "WORKSPACE", Workspace(root, "Hsx Work"))

    status, _, body = _request(
        "/api/upload",
        method="POST",
        payload={"filename": "notes.txt", "content": "local attachment content"},
    )
    result = json.loads(body)
    assert status == 200
    assert result["success"] is True
    assert result["path"].startswith("Hsx Work/")
    uploaded = server.WORKSPACE.resolve_read(result["path"])
    assert uploaded.read_text(encoding="utf-8") == "local attachment content"


def test_binary_upload_preserves_bytes_and_rejects_invalid_base64(tmp_path: Path, monkeypatch):
    root = tmp_path / "share"
    root.mkdir()
    monkeypatch.setattr(server, "WORKSPACE", Workspace(root, "Hsx Work"))
    content = b"\x00\xff\x10binary\x80data"

    status, _, body = _request(
        "/api/upload",
        method="POST",
        payload={"filename": "archive.bin", "content_base64": base64.b64encode(content).decode("ascii")},
    )
    result = json.loads(body)
    assert status == 200
    assert server.WORKSPACE.resolve_read(result["path"]).read_bytes() == content

    status, _, body = _request(
        "/api/upload",
        method="POST",
        payload={"filename": "broken.bin", "content_base64": "not-base64!"},
    )
    assert status == 400
    assert json.loads(body)["error"] == "File data is invalid."


def test_chat_stream_forwards_model_text_deltas(tmp_path: Path, monkeypatch):
    root = tmp_path / "share"
    root.mkdir()
    monkeypatch.setattr(server, "WORKSPACE", Workspace(root, "Hsx Work"))
    monkeypatch.setattr(server, "WORK_DIR", root / "Hsx Work")
    monkeypatch.setattr(server, "ASK_JOBS", AskUserJobs(tmp_path / "jobs.db"))
    monkeypatch.setattr(server.POLICY, "audit_event", lambda *args: None)

    observed_targets = []

    def fake_run_chat_job(
        job_id, approvals, sensitive, target_device, on_text_delta, cancel_event=None, on_provider_notice=None,
        on_activity_event=None,
    ):
        del cancel_event, on_activity_event
        observed_targets.append(target_device)
        on_text_delta("Streaming ")
        on_text_delta("answer")
        completed = server.ASK_JOBS.complete(job_id, "Streaming answer", [])
        server._emit_chat_event(job_id, "job", server._ask_job_payload(completed))
        server._emit_chat_event(job_id, "done", {})

    monkeypatch.setattr(server, "_run_chat_job", fake_run_chat_job)
    status, headers, body = _request(
        "/api/chat/stream",
        method="POST",
        payload={"message": "Say hello", "target_device": "local_host"},
        stop_event="done",
    )

    text = body.decode()
    assert status == 200
    assert headers["Content-Type"] == "text/event-stream"
    assert "event: acknowledgement" not in text
    assert "event: text_delta\ndata: {\"text\":\"Streaming \"}" in text
    assert "event: text_delta\ndata: {\"text\":\"answer\"}" in text
    assert '"result":"Streaming answer"' in text
    assert observed_targets == ["local_host"]


def test_files_panel_rejects_traversal(tmp_path: Path, monkeypatch):
    root = tmp_path / "share"
    (root / "Hsx Work").mkdir(parents=True)
    (tmp_path / "secret.txt").write_text("not exposed", encoding="utf-8")
    monkeypatch.setattr(server, "WORKSPACE", Workspace(root, "Hsx Work"))

    status, _, _ = _request("/api/files/content?path=..%2Fsecret.txt")
    assert status == 404


def test_files_panel_requires_bearer_when_origin_is_missing(tmp_path, monkeypatch):
    (tmp_path / "Hsx Work").mkdir()
    monkeypatch.setattr(server, "WORKSPACE", Workspace(tmp_path, "Hsx Work"))
    status, _, _ = _request("/api/files", tokenless=True)
    assert status == 401
    status, _, _ = _request("/api/files")
    assert status == 200


def test_post_request_body_is_bounded():
    oversized = "x" * (8 * 1024 * 1024 + 1)
    status, _, body = _request(
        "/api/chat/stream",
        method="POST",
        payload={"message": oversized},
    )
    assert status == 413
    assert b"Request is too large" in body


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
    assert response["response"] == ""
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
    while time.monotonic() < deadline:
        complete = jobs.get(job_id)
        if (
            complete["state"] == "done"
            and notifications
            and notifications[-1][0] == "job_done"
        ):
            break
        time.sleep(0.01)
    complete = jobs.get(job_id)
    assert complete["state"] == "done"
    assert complete["result"] == "The report is ready."
    assert calls == [None, {"tool_call_id": "ask-call-1", "answer": "layout.docx"}]
    assert notifications[0][0:2] == ("needs_input", "Hsx needs your input")
    assert all(event != "job_done" for event, _, _ in notifications)
