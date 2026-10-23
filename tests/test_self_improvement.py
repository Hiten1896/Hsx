from pathlib import Path

import pytest

from agent.approval import ApprovalGuard
from agent.self_improvement import SelfImprovement


def test_proposal_requires_tests_and_owner_approval(tmp_path: Path):
    approval_token = "test-approval-token"
    guard = ApprovalGuard(tmp_path / "owner.pin", approval_token)
    manager = SelfImprovement(
        tmp_path / "improvements.db",
        guard,
    )
    proposal = manager.create("Better parser", "Improve parsing safely.")
    manager.prototype(proposal["id"], "test_feature.py", "def test_feature():\n    assert 2 + 2 == 4\n")
    checked = manager.run_strict_checks(proposal["id"])
    assert checked["status"] == "awaiting_approval"
    with pytest.raises(PermissionError):
        manager.approve(proposal["id"], "Someone else")
    approved = manager.approve(proposal["id"], approval_token)
    assert approved["status"] == "approved"
