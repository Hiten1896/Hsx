import json
from pathlib import Path

from agent.approval import ApprovalGuard
from agent.policy import PolicyEngine


def test_autonomy_tiers_control_auto_and_ask_modes(tmp_path: Path, monkeypatch):
    policy = PolicyEngine(tmp_path / "audit.jsonl")
    monkeypatch.setenv("HSX_AUTONOMY", "auto")

    assert policy.decide({"name": "system_info", "risk": "dangerous"}, {}, "s")["allowed"]
    assert policy.decide(
        {"name": "make_markdown", "risk": "dangerous"},
        {"filename": "report.md"},
        "s",
    )["allowed"]
    assert policy.decide(
        {"name": "system_execute"},
        {"action": "exec", "command": "sudo rm -rf /tmp/archive"},
        "s",
    )["status"] == "waiting_approval"
    assert policy.decide(
        {"name": "system_execute"},
        {"action": "exec", "command": "rm -f logs/audit.jsonl"},
        "s",
    )["status"] == "denied"

    monkeypatch.setenv("HSX_AUTONOMY", "ask")
    assert policy.decide(
        {"name": "make_markdown", "risk": "safe"},
        {"filename": "report.md"},
        "s",
    )["allowed"]


def test_dangerous_action_always_asks_and_yes_is_audited(tmp_path: Path, monkeypatch):
    audit = tmp_path / "audit.jsonl"
    policy = PolicyEngine(audit, ApprovalGuard(tmp_path / "owner.pin", "123456"))
    monkeypatch.setenv("HSX_AUTONOMY", "auto")
    monkeypatch.setenv("HSX_ALLOW_DANGEROUS", "1")
    tool = {"name": "system_execute", "risk": "safe"}
    arguments = {"action": "service_action", "service": "ssh", "service_action": "restart"}

    approval = policy.decide(tool, arguments, "session")
    assert approval["status"] == "waiting_approval"
    assert "choices" not in approval["approval_request"]
    assert "advisory" not in approval["approval_request"]
    assert policy.decide(tool, arguments, "session", True, approver="owner")["allowed"]

    entry = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["tier"] == "DANGEROUS"
    assert entry["decision"] == "approved"
    assert entry["approver"] == "owner"


def test_blocked_tools_cannot_be_approved(tmp_path: Path, monkeypatch):
    policy = PolicyEngine(tmp_path / "audit.jsonl")
    monkeypatch.setenv("HSX_AUTONOMY", "auto")
    result = policy.decide(
        {"name": "system_execute"},
        {"action": "exec", "command": "rm -f logs/audit.jsonl"},
        "session",
        approved=True,
        approver="owner",
    )
    assert result == {"allowed": False, "status": "denied"}


def test_audit_log_does_not_store_argument_values(tmp_path: Path, monkeypatch):
    audit = tmp_path / "audit.jsonl"
    policy = PolicyEngine(audit)
    monkeypatch.setenv("HSX_AUTONOMY", "auto")
    policy.decide(
        {"name": "system_execute"},
        {"action": "read_file", "path": "/home/hsxshare/owner-secret.txt"},
        "session",
    )
    serialized = audit.read_text(encoding="utf-8")
    assert "owner-secret.txt" not in serialized
    assert '"tier": "DANGEROUS"' in serialized
