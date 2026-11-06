import json
import copy

import pytest

from agent import orchestrator
from agent.ask_user import ApprovalRequest
from agent.llm_client import NormalizedBlock, NormalizedResponse
from agent.policy import PolicyEngine


def test_model_history_gets_pause_marker_not_approval_payload(tmp_path, monkeypatch):
    policy = PolicyEngine(tmp_path / "audit.jsonl")
    monkeypatch.setattr(orchestrator, "POLICY", policy)
    monkeypatch.setattr(orchestrator.INTENT_ROUTER, "predict_with_confidence", lambda _message: ("system_execute", 0.9))
    monkeypatch.setattr(orchestrator.MEMORY, "prompt_context", lambda _message: "")
    monkeypatch.setattr(orchestrator.MEMORY, "record_message", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(orchestrator.EXPERIENCE_MEMORY, "prompt_context", lambda _message: "")
    monkeypatch.setattr(orchestrator, "system_body_context", lambda _target: "")
    tool = {
        "name": "system_execute",
        "risk": "dangerous",
        "input_schema": {
            "type": "object",
            "properties": {"action": {"type": "string"}, "command": {"type": "string"}},
            "required": ["action", "command"],
        },
    }
    response = NormalizedResponse([NormalizedBlock(
        type="tool_use",
        id="delete-call",
        name="system_execute",
        input={"action": "exec", "command": "rm /tmp/old.txt"},
    )])
    monkeypatch.setattr(orchestrator, "call_llm", lambda *_args, **_kwargs: response)

    with pytest.raises(ApprovalRequest) as paused:
        orchestrator.run_agent(
            "delete the old file",
            [],
            [tool],
            {"system_execute": lambda **_kwargs: pytest.fail("dangerous command ran before approval")},
            session_id="approval-history",
            sensitive=True,
            enable_planning=False,
        )

    serialized = json.dumps(paused.value.checkpoint)
    assert "Paused: waiting for user approval." in serialized
    assert "waiting_approval" not in serialized
    assert "approval_request" not in serialized
    assert "choices" not in serialized


def test_resume_adds_only_tool_result_and_continues_turn(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "POLICY", PolicyEngine(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(orchestrator.INTENT_ROUTER, "predict_with_confidence", lambda _message: ("", 0.0))
    monkeypatch.setattr(orchestrator.MEMORY, "prompt_context", lambda _message: "")
    monkeypatch.setattr(orchestrator.EXPERIENCE_MEMORY, "prompt_context", lambda _message: "")
    monkeypatch.setattr(orchestrator, "system_body_context", lambda _target: "")
    seen = []

    def finish(messages, *_args, **_kwargs):
        seen.append(copy.deepcopy(messages))
        return NormalizedResponse([NormalizedBlock(type="text", text="The action was declined.")])

    monkeypatch.setattr(orchestrator, "call_llm", finish)
    checkpoint = [
        {"role": "user", "content": "delete the old file"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "delete-call", "name": "system_execute", "input": {}},
            {"type": "text", "text": "Paused: waiting for user approval."},
        ]},
    ]

    answer, history = orchestrator.run_agent(
        "delete the old file",
        checkpoint,
        [],
        {},
        session_id="approval-resume",
        sensitive=True,
        resume_tool_result={
            "tool_call_id": "delete-call",
            "result": {"success": False, "error": "The user declined this action."},
        },
        enable_planning=False,
    )

    assert answer == "The action was declined."
    assert seen[0][-1]["content"][0]["tool_use_id"] == "delete-call"
    assert "approval_request" not in json.dumps(history)
