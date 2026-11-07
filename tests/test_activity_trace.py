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


def test_activity_arguments_redact_credentials_and_bound_content():
    value = redact_activity_value({
        "command": "curl https://example.test?token=abc123",
        "api_key": "do-not-show",
    })
    assert value["api_key"] == "[REDACTED]"
    assert "abc123" not in value["command"]
    assert len(redact_activity_value("ordinary text " * 10, limit=20)) == 20
