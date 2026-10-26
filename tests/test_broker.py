import json
from pathlib import Path

from broker.interfaces import FakeSystemInterface
from broker.server import Broker


POLICY = Path(__file__).parents[1] / "deploy" / "policy.yaml"


def make_broker(tmp_path: Path, *, uid: int = 1000) -> Broker:
    return Broker(
        POLICY,
        tmp_path / "audit.jsonl",
        system=FakeSystemInterface(uid=uid),
        expected_uid=1000,
    )


def test_wrong_peer_uid_is_denied_and_audited(tmp_path: Path):
    broker = make_broker(tmp_path, uid=2000)
    response = broker.handle({"id": "1", "action": "system_stats", "args": {}}, peer_uid=2000)
    assert response["ok"] is False
    assert "hsx user" in response["error"]
    assert json.loads((tmp_path / "audit.jsonl").read_text())["decision"] == "deny"


def test_unknown_action_and_schema_violation_are_denied(tmp_path: Path):
    broker = make_broker(tmp_path)
    assert broker.handle({"id": "1", "action": "shell", "args": {}}, peer_uid=1000)["ok"] is False
    assert broker.handle({"id": "2", "action": "system_stats", "args": {"command": "id"}}, peer_uid=1000)["ok"] is False


def test_argument_injection_and_path_traversal_are_denied(tmp_path: Path):
    broker = make_broker(tmp_path)
    for value in ("apt;id", "$(id)", "../memory"):
        response = broker.handle(
            {"id": value, "action": "pip_install", "args": {"package": value}},
            peer_uid=1000,
        )
        assert response["ok"] is False


def test_shadow_mode_does_not_execute_mutating_action(tmp_path: Path):
    system = FakeSystemInterface(uid=1000)
    broker = Broker(POLICY, tmp_path / "audit.jsonl", system=system, expected_uid=1000)
    response = broker.handle(
        {"id": "1", "action": "service", "args": {"operation": "restart", "unit": "hsx-agent.service"}},
        peer_uid=1000,
    )
    assert response == {"id": "1", "ok": False, "decision": "would_run", "result": "shadow mode"}
    assert system.calls == []


def test_non_hsx_service_requires_approval(tmp_path: Path):
    broker = make_broker(tmp_path)
    response = broker.handle(
        {"id": "1", "action": "service", "args": {"operation": "restart", "unit": "sshd.service"}},
        peer_uid=1000,
    )
    assert response["decision"] == "approval_required"


def test_read_only_action_runs_in_shadow_mode(tmp_path: Path):
    system = FakeSystemInterface(uid=1000)
    broker = Broker(POLICY, tmp_path / "audit.jsonl", system=system, expected_uid=1000)
    response = broker.handle({"id": "1", "action": "system_stats", "args": {}}, peer_uid=1000)
    assert response["ok"] is True
    assert response["decision"] == "allow"
    assert system.calls == []


def test_approval_and_deny_tiers_do_not_execute(tmp_path: Path):
    system = FakeSystemInterface(uid=1000)
    broker = Broker(POLICY, tmp_path / "audit.jsonl", system=system, expected_uid=1000)
    approval = broker.handle({"id": "1", "action": "apt_install", "args": {"package": "curl"}}, peer_uid=1000)
    denied = broker.handle({"id": "2", "action": "system_stats", "args": {}}, peer_uid=1000)
    assert approval["decision"] == "approval_required"
    assert denied["decision"] == "allow"
    assert system.calls == []


def test_missing_policy_fails_closed(tmp_path: Path):
    broker = Broker(tmp_path / "missing.yaml", tmp_path / "audit.jsonl", system=FakeSystemInterface(uid=1000), expected_uid=1000)
    response = broker.handle({"id": "1", "action": "system_stats", "args": {}}, peer_uid=1000)
    assert response["ok"] is False
    assert response["decision"] == "deny"


def test_deny_policy_is_respected(tmp_path: Path):
    policy = tmp_path / "policy.yaml"
    policy.write_text("autonomy: auto\nactions:\n  system_stats:\n    tier: deny\n", encoding="utf-8")
    broker = Broker(policy, tmp_path / "audit.jsonl", system=FakeSystemInterface(uid=1000), expected_uid=1000)
    response = broker.handle({"id": "1", "action": "system_stats", "args": {}}, peer_uid=1000)
    assert response["ok"] is False
    assert response["decision"] == "deny"
