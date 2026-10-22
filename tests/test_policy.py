import json
from pathlib import Path

from agent.policy import PolicyEngine


def skill(**values):
    return {"name": "example", "risk": "safe", **values}


def test_advisory_proceed_cancel_and_audit(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    policy = PolicyEngine(audit)
    request = policy.decide(
        skill(advisory="This has a legal concern.", requires_acknowledgement=True),
        {"url": "https://example.test/a"},
        "session-1",
    )
    assert request["status"] == "waiting_approval"
    assert request["approval_request"]["choices"][0] == "I understand, proceed"
    assert policy.decide(skill(advisory="This has a legal concern.", requires_acknowledgement=True), {"url": "https://example.test/a"}, "session-1", True)["allowed"]
    assert policy.decide(skill(advisory="This has a legal concern.", requires_acknowledgement=True), {"url": "https://example.test/a"}, "session-1")["allowed"]
    assert policy.decide(skill(advisory="This has a legal concern.", requires_acknowledgement=True), {"url": "https://example.test/b"}, "session-1")["status"] == "waiting_approval"
    assert policy.decide(skill(advisory="This has a legal concern.", requires_acknowledgement=True), {"url": "https://example.test/c"}, "session-1", False)["status"] == "cancelled"
    entries = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert any(entry["decision"] == "proceed" and entry["advisory"] for entry in entries)
    assert any(entry["decision"] == "cancel" for entry in entries)


def test_dangerous_always_asks_and_advisory_limit(tmp_path: Path, monkeypatch):
    policy = PolicyEngine(tmp_path / "audit.jsonl")
    dangerous = skill(risk="dangerous", advisory="Danger")
    monkeypatch.setenv("HSX_ALLOW_DANGEROUS", "1")
    assert policy.decide(dangerous, {}, "s", True)["allowed"]
    assert policy.decide(dangerous, {}, "s")["status"] == "waiting_approval"
    try:
        policy.decide(skill(advisory="x" * 300), {}, "s")
    except ValueError:
        pass
    else:
        raise AssertionError("long advisory was accepted")
