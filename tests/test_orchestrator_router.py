import json
import threading
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
            "input_schema": {"type": "object", "properties": {}, "required": []},
        }],
        {"system_status": system_status},
        session_id="fast-session",
        job_id="fast-job",
    )

    assert calls == ["called"]
    assert answer == "The request completed, but returned no readable details."
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


def test_run_scoped_cancel_does_not_leak_to_the_next_run(monkeypatch):
    _setup(monkeypatch, "system_status", 0.99)
    calls = []
    tool = {"name": "system_status", "input_schema": {
        "type": "object", "properties": {}, "required": [],
    }}
    cancelled = threading.Event()
    cancelled.set()
    answer, _ = orchestrator.run_agent(
        "Check system status", [], [tool], {"system_status": lambda: calls.append("called")},
        cancel_event=cancelled,
    )
    assert answer == "This run was stopped."
    assert calls == []

    answer, _ = orchestrator.run_agent(
        "Check system status", [], [tool],
        {"system_status": lambda: calls.append("called") or {"success": True}},
        cancel_event=threading.Event(),
    )
    assert calls == ["called"]
    assert answer == "The request completed, but returned no readable details."


def test_raw_json_model_answer_becomes_a_readable_file_list():
    answer = orchestrator._friendly_model_answer(
        '{"success": true, "files": [{"name": "report.pdf"}, {"path": "notes.md"}]}'
    )
    assert answer == "I found these files:\n• report.pdf\n• notes.md"
    assert not answer.lstrip().startswith("{")


def test_weather_request_calls_registered_web_search(monkeypatch):
    _setup(monkeypatch, "system_status", 0.1)
    queries = []
    monkeypatch.setattr(orchestrator, "call_llm", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("weather lookup should route to web_search")
    ))
    answer, _ = orchestrator.run_agent(
        "What is the weather in Delhi?", [],
        [{"name": "web_search", "input_schema": {
            "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
        }}],
        {"web_search": lambda query: queries.append(query) or {"success": True, "results": ["Sunny"]}},
    )
    assert queries == ["What is the weather in Delhi?"]
    assert "Sunny" in answer


def test_describe_image_rejects_an_unmentioned_path_before_tool_execution(monkeypatch):
    _setup(monkeypatch, "describe_image", 0.1)
    responses = iter([
        NormalizedResponse([
            NormalizedBlock(
                type="tool_use",
                name="describe_image",
                input={"path": "private/secret.png", "question": "What is shown?"},
                id="image-call",
            ),
        ]),
        NormalizedResponse([NormalizedBlock(type="text", text="I can only describe images you name or attach.")]),
    ])
    monkeypatch.setattr(orchestrator, "call_llm", lambda *args, **kwargs: next(responses))
    calls = []

    answer, _ = orchestrator.run_agent(
        "Describe the screenshot.",
        [],
        [{
            "name": "describe_image",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "question": {"type": "string"},
                },
                "required": ["path", "question"],
            },
        }],
        {"describe_image": lambda **kwargs: calls.append(kwargs)},
        job_id="unmentioned-image",
    )

    assert calls == []
    assert answer == "I can only describe images you name or attach."


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
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        }],
        {"read_file": lambda path: {"path": path}},
        job_id="argument-job",
    )

    assert answer == "Need a path."
    assert llm_calls == [True]


def test_high_confidence_unlisted_tool_runs_without_policy_prompt(monkeypatch):
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
        [{"name": "system_status", "input_schema": {"type": "object", "properties": {}, "required": []}}],
        {"system_status": lambda: {"success": True}},
        job_id="policy-job",
    )

    assert "completed" in answer.casefold()
    assert llm_calls == []
