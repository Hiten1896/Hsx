import json
import time
from pathlib import Path

import server
from agent import orchestrator
from agent.ask_user import AskUserJobs
from agent.llm_client import NormalizedBlock, NormalizedResponse
from agent.router import RouterEventLogger
from agent.workspace import Workspace
from tests.test_files_panel import _request


def _read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_router_logs_predicted_tool_and_execution_time(tmp_path, monkeypatch):
    log_path = tmp_path / "router.jsonl"
    monkeypatch.setattr(orchestrator, "ROUTER_LOGGER", RouterEventLogger(log_path))
    monkeypatch.setattr(orchestrator.MEMORY, "prompt_context", lambda prompt: "")
    monkeypatch.setattr(orchestrator.MEMORY, "record_message", lambda *args: None)
    monkeypatch.setattr(orchestrator.POLICY, "decide", lambda *args: {"allowed": True})
    monkeypatch.setattr(
        orchestrator.INTENT_ROUTER,
        "predict_with_confidence",
        lambda prompt: ("lookup", 0.5),
    )

    responses = iter([
        NormalizedResponse([
            NormalizedBlock(type="tool_use", name="lookup", input={"query": "news"}, id="call-1"),
        ]),
        NormalizedResponse([NormalizedBlock(type="text", text="Found it.")]),
    ])
    monkeypatch.setattr(orchestrator, "call_llm", lambda *args, **kwargs: next(responses))

    def lookup(query):
        time.sleep(0.01)
        return {"query": query}

    answer, _ = orchestrator.run_agent(
        "Find news.",
        [],
        [{"name": "lookup"}],
        {"lookup": lookup},
        session_id="test-session",
        job_id="test-job",
    )

    assert answer == "Found it."
    events = _read_events(log_path)
    assert [event["event"] for event in events] == ["intent_prediction", "tool_predicted", "tool_execution"]
    assert events[0]["tool"] == "lookup"
    assert events[0]["confidence"] == 0.5
    assert events[0]["route"] == "llm"
    assert events[1]["tool"] == "lookup"
    assert events[1]["tool_call_id"] == "call-1"
    assert events[2]["tool"] == "lookup"
    assert events[2]["tool_call_id"] == "call-1"
    assert events[2]["duration_ms"] >= 5
    assert events[2]["status"] == "succeeded"
    assert {event["job_id"] for event in events} == {"test-job"}


def test_chat_prompt_and_good_bad_feedback_are_logged(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    log_path = tmp_path / "router.jsonl"
    monkeypatch.setattr(server, "WORKSPACE", Workspace(root, "Hsx Work"))
    monkeypatch.setattr(server, "WORK_DIR", root / "Hsx Work")
    monkeypatch.setattr(server, "ASK_JOBS", AskUserJobs(tmp_path / "jobs.db"))
    monkeypatch.setattr(server, "ROUTER_LOGGER", RouterEventLogger(log_path))
    monkeypatch.setattr(server, "SESSION_LAST_JOBS", {})
    monkeypatch.setattr(server.POLICY, "audit_event", lambda *args: None)
    monkeypatch.setattr(server, "_run_chat_job", lambda *args, **kwargs: None)

    status, _, body = _request(
        "/api/chat",
        method="POST",
        payload={"message": "Summarize this document.", "session_id": "feedback-session"},
    )
    assert status == 202
    job_id = json.loads(body)["job"]["id"]

    for command in ("/good", "/bad"):
        status, _, body = _request(
            "/api/chat",
            method="POST",
            payload={"message": command, "session_id": "feedback-session"},
        )
        assert status == 200
        assert json.loads(body)["feedback"] == command[1:]

    events = _read_events(log_path)
    assert events[0]["event"] == "user_prompt"
    assert events[0]["prompt"] == "Summarize this document."
    assert events[0]["job_id"] == job_id
    assert [(event["event"], event["feedback"]) for event in events[1:]] == [
        ("user_feedback", "good"),
        ("user_feedback", "bad"),
    ]
    assert all(event["job_id"] == job_id for event in events)
