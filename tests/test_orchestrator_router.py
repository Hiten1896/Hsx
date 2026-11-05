import json
from pathlib import Path

from agent import orchestrator
from agent.llm_client import NormalizedBlock, NormalizedResponse
from agent.router import RouterEventLogger


class FixedIntentRouter:
    def __init__(self, tool: str, confidence: float):
        self.tool = tool
        self.confidence = confidence

    def predict_with_confidence(self, prompt: str) -> tuple[str, float]:
        return self.tool, self.confidence


def _setup(monkeypatch, tool: str, confidence: float):
    monkeypatch.setattr(orchestrator, "INTENT_ROUTER", FixedIntentRouter(tool, confidence))
    monkeypatch.setattr(orchestrator.MEMORY, "prompt_context", lambda prompt: "")
    monkeypatch.setattr(orchestrator.MEMORY, "record_message", lambda *args: None)
    monkeypatch.setattr(orchestrator.POLICY, "is_stopped", lambda: False)
    monkeypatch.setattr(orchestrator.POLICY, "decide", lambda *args: {"allowed": True})


def test_high_confidence_safe_zero_argument_tool_runs_without_llm(tmp_path, monkeypatch):
    _setup(monkeypatch, "system_status", 0.91)
    log_path = tmp_path / "router.jsonl"
    monkeypatch.setattr(orchestrator, "ROUTER_LOGGER", RouterEventLogger(log_path))
    calls = []

    def system_status():
        calls.append("called")
        return {"success": True, "status": "online"}

    def unexpected_llm(*args, **kwargs):
        raise AssertionError("high-confidence eligible intent should bypass the LLM")

    monkeypatch.setattr(orchestrator, "call_llm", unexpected_llm)
    answer, history = orchestrator.run_agent(
        "Check the system status.",
        [],
        [{
            "name": "system_status",
            "risk": "safe",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        }],
        {"system_status": system_status},
        session_id="fast-session",
        job_id="fast-job",
    )

    assert calls == ["called"]
    assert json.loads(answer) == {"success": True, "status": "online"}
    assert [message["role"] for message in history] == ["user", "assistant", "user", "assistant"]
    assert history[1]["content"][0]["type"] == "tool_use"
    assert history[2]["content"][0]["type"] == "tool_result"
    events = [json.loads(line) for line in Path(log_path).read_text(encoding="utf-8").splitlines()]
    assert events[0]["event"] == "intent_prediction"
    assert events[0]["route"] == "direct"
    assert events[0]["confidence"] == 0.91
    assert events[1]["event"] == "tool_predicted"
    assert events[2]["event"] == "tool_execution"
    assert events[2]["status"] == "succeeded"


def test_router_is_not_called_when_no_tools_are_available(monkeypatch):
    class UnexpectedIntentRouter:
        def predict_with_confidence(self, prompt):
            raise AssertionError("there are no tools to route to")

    monkeypatch.setattr(orchestrator, "INTENT_ROUTER", UnexpectedIntentRouter())
    monkeypatch.setattr(orchestrator.MEMORY, "prompt_context", lambda prompt: "")
    monkeypatch.setattr(orchestrator.MEMORY, "record_message", lambda *args: None)
    monkeypatch.setattr(orchestrator.POLICY, "is_stopped", lambda: False)
    monkeypatch.setattr(
        orchestrator,
        "call_llm",
        lambda *args, **kwargs: NormalizedResponse([NormalizedBlock(type="text", text="Hello.")]),
    )

    answer, _ = orchestrator.run_agent("Hello.", [], [], {})

    assert answer == "Hello."


def test_low_confidence_uses_llm_loop(monkeypatch):
    _setup(monkeypatch, "search_files", 0.84)
    llm_calls = []
    monkeypatch.setattr(
        orchestrator,
        "call_llm",
        lambda *args, **kwargs: (
            llm_calls.append(True)
            or NormalizedResponse([NormalizedBlock(type="text", text="LLM answer")])
        ),
    )
    answer, _ = orchestrator.run_agent("Find this file.", [], [], {}, job_id="llm-job")

    assert answer == "LLM answer"
    assert llm_calls == [True]


def test_high_confidence_tool_with_required_arguments_uses_llm(monkeypatch):
    _setup(monkeypatch, "read_file", 0.99)
    llm_calls = []
    monkeypatch.setattr(
        orchestrator,
        "call_llm",
        lambda *args, **kwargs: (
            llm_calls.append(True)
            or NormalizedResponse([NormalizedBlock(type="text", text="Need a path.")])
        ),
    )
    answer, _ = orchestrator.run_agent(
        "Read that file.",
        [],
        [{
            "name": "read_file",
            "risk": "safe",
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        }],
        {"read_file": lambda path: {"path": path}},
        job_id="argument-job",
    )

    assert answer == "Need a path."
    assert llm_calls == [True]


def test_high_confidence_unapproved_tool_uses_llm(monkeypatch):
    _setup(monkeypatch, "system_status", 0.99)
    llm_calls = []
    monkeypatch.setattr(
        orchestrator,
        "call_llm",
        lambda *args, **kwargs: (
            llm_calls.append(True)
            or NormalizedResponse([NormalizedBlock(type="text", text="Safe fallback.")])
        ),
    )
    answer, _ = orchestrator.run_agent(
        "Check status.",
        [],
        [{
            "name": "system_status",
            "risk": "confirm",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        }],
        {"system_status": lambda: {"success": True}},
        job_id="risk-job",
    )

    assert answer == "Safe fallback."
    assert llm_calls == [True]
