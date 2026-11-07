import pytest

from agent.activity_trace import ACTIVITY_MAP, redact_activity_value, tool_end_event, tool_start_event


def test_every_registered_tool_has_an_activity_mapping():
    import server

    names = {tool["name"] for tool in server.TOOLS}
    assert names <= ACTIVITY_MAP.keys(), f"Missing activity mappings: {sorted(names - ACTIVITY_MAP.keys())}"


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("read_file", {"path": "Hsx Work/RAG_Document_Agent.pdf"}, ("book", "Reading", "RAG_Document_Agent.pdf")),
        ("run_code", {"command": "python build_slides.py"}, ("terminal", "Running", "python build_slides.py")),
        ("web_search", {"query": "weather in Delhi"}, ("search", "Searching", "weather in Delhi")),
        ("describe_image", {"path": "images/one.png"}, ("images", "Viewing", "one.png")),
    ],
)
def test_tool_start_event_has_icon_verb_and_safe_target(name, arguments, expected):
    event = tool_start_event(name, arguments, "call-1")
    assert (event["icon"], event["present"], event["target"]) == expected


def test_tool_end_event_reports_duration_failure_and_image_paths():
    start = tool_start_event("describe_image", {"path": "images/one.png"}, "call-1")
    event = tool_end_event(start, {"success": True, "message": "Viewed image"}, 1240)
    assert event["past"] == "Viewed"
    assert event["duration_ms"] == 1240
    assert event["images"] == ["images/one.png"]

    failed = tool_end_event(start, {"success": False, "error": "Image unavailable"}, 10)
    assert failed["status"] == "failed"
    assert failed["output"] == "Image unavailable"


def test_capability_activity_event_carries_grouped_report():
    start = tool_start_event("capabilities", {}, "capability-call")
    report = {
        "success": True,
        "groups": [{
            "name": "Providers",
            "items": [{
                "name": "Gemini",
                "status": "needs setup",
                "detail": "Add GEMINI_API_KEY.",
            }],
        }],
        "cannot_do": [{"name": "OCR", "how_to_add": "Add OCR."}],
    }

    event = tool_end_event(start, report, 10)

    assert event["capabilities"]["groups"][0]["items"][0]["status"] == "needs setup"
    assert event["capabilities"]["cannot_do"][0]["how_to_add"] == "Add OCR."


def test_activity_arguments_redact_credentials_and_bound_content():
    value = redact_activity_value({
        "command": "curl https://example.test?token=abc123",
        "api_key": "do-not-show",
    })
    assert value["api_key"] == "[REDACTED]"
    assert "abc123" not in value["command"]
    assert len(redact_activity_value("ordinary text " * 10, limit=20)) == 20


def test_unified_activity_diff_has_three_context_lines_line_numbers_and_redaction():
    import server

    before = {"path": "report.txt", "binary": False, "text": "one\ntwo\nsecret=old-value\nfour\nfive"}
    after = {"path": "report.txt", "binary": False, "text": "one\ntwo\nsecret=new-value\nfour\nsix"}
    diff = server._activity_diff(before, after, "report.txt")
    assert diff["added"] == 2
    assert diff["removed"] == 2
    lines = [line for hunk in diff["hunks"] for line in hunk["lines"]]
    assert lines[0]["old_line"] == lines[0]["new_line"] == 1
    assert all("old-value" not in line["text"] and "new-value" not in line["text"] for line in lines)
    assert sum(line["kind"] == "context" for line in lines) >= 3


def test_created_and_binary_activity_files_have_distinct_diff_shapes():
    import server

    created = server._activity_diff(
        None,
        {"path": "report.txt", "binary": False, "text": "first\nsecond"},
        "report.txt",
    )
    assert created["added"] == 2
    assert created["removed"] == 0
    assert [line["kind"] for hunk in created["hunks"] for line in hunk["lines"]] == ["added", "added"]

    binary = server._activity_diff(
        None,
        {"path": "report.pdf", "binary": True, "size": 1536, "text": ""},
        "report.pdf",
    )
    assert binary["binary"] is True
    assert binary["size_bytes"] == 1536


def test_activity_thumbnail_is_small_and_path_jailed(tmp_path, monkeypatch):
    import io
    import server
    from PIL import Image
    from agent.workspace import Workspace
    from tests.test_files_panel import _request

    root = tmp_path / "share"
    root.mkdir()
    monkeypatch.setattr(server, "WORKSPACE", Workspace(root, "Hsx Work"))
    image_path = root / "large.png"
    Image.new("RGB", (800, 600), "green").save(image_path)
    monkeypatch.setitem(server._ACTIVITY_IMAGE_PATHS, "activity-image-test", image_path)

    status, headers, body = _request("/api/activity/image?id=activity-image-test&thumbnail=1")
    assert status == 200
    assert headers["Content-Type"] == "image/jpeg"
    with Image.open(io.BytesIO(body)) as thumbnail:
        assert max(thumbnail.size) <= 256

    outside = tmp_path / "outside.png"
    Image.new("RGB", (10, 10), "red").save(outside)
    monkeypatch.setitem(server._ACTIVITY_IMAGE_PATHS, "outside-image-test", outside)
    status, _, _ = _request("/api/activity/image?id=outside-image-test&thumbnail=1")
    assert status == 404


def test_retry_activity_rechecks_policy_before_running_tool(monkeypatch):
    import server
    from types import SimpleNamespace

    call_id = "retry-policy-test"
    retry = {
        "job_id": "job-1",
        "session_id": "session-1",
        "tool": "run_code",
        "arguments": {"command": "python safe.py"},
        "start": tool_start_event("run_code", {"command": "python safe.py"}, call_id),
        "created_at": server.time.time(),
        "target_device": "local_host",
    }
    calls = []

    class DenyPolicy:
        def decide(self, tool, arguments, session_id):
            calls.append((tool["name"], arguments, session_id))
            return {"allowed": False, "status": "blocked"}

    monkeypatch.setattr(server, "_ACTIVITY_RETRY_CALLS", {call_id: retry})
    monkeypatch.setattr(server, "ASK_JOBS", SimpleNamespace(get=lambda job_id: {"session_id": "session-1"}))
    monkeypatch.setattr(server, "TOOLS", [{"name": "run_code"}])
    monkeypatch.setattr(server, "SKILL_MAP", {"run_code": lambda **_arguments: pytest.fail("blocked retry ran")})
    monkeypatch.setattr(server, "POLICY", DenyPolicy())

    result, status = server._retry_activity_tool("job-1", call_id)

    assert status == 403
    assert result["status"] == "blocked"
    assert calls == [("run_code", {"command": "python safe.py"}, "session-1")]


def test_retry_activity_success_records_the_finished_step(monkeypatch):
    import server
    from types import SimpleNamespace

    call_id = "retry-success-test"
    retry = {
        "job_id": "job-2",
        "session_id": "session-2",
        "tool": "run_code",
        "arguments": {"command": "python safe.py"},
        "start": tool_start_event("run_code", {"command": "python safe.py"}, call_id),
        "created_at": server.time.time(),
        "target_device": "local_host",
        "task_dir": "",
    }

    class AllowPolicy:
        def decide(self, tool, arguments, session_id):
            return {"allowed": True}

        def audit_event(self, *args):
            return None

    class RouterLogger:
        def log_tool_prediction(self, *args):
            return None

        def log_tool_execution(self, *args):
            return None

    monkeypatch.setattr(server, "_ACTIVITY_RETRY_CALLS", {call_id: retry})
    monkeypatch.setattr(server, "ASK_JOBS", SimpleNamespace(get=lambda job_id: {"session_id": "session-2"}))
    monkeypatch.setattr(server, "TOOLS", [{"name": "run_code"}])
    monkeypatch.setattr(server, "SKILL_MAP", {"run_code": lambda **_arguments: {"success": True, "message": "done"}})
    monkeypatch.setattr(server, "POLICY", AllowPolicy())
    monkeypatch.setattr(server, "ROUTER_LOGGER", RouterLogger())

    result, status = server._retry_activity_tool("job-2", call_id)

    assert status == 200
    assert result["activity"]["status"] == "completed"
    assert result["activity"]["output"] == "done"
    assert call_id not in server._ACTIVITY_RETRY_CALLS
