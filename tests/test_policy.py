import json

import pytest
import yaml

from agent.autonomy import classify_action
from agent.hardware.camera import camera_session_owner
from agent.policy import PolicyEngine


def _policy(tmp_path):
    permissions = tmp_path / "permissions.yaml"
    permissions.write_text("ask_first:\n  - install_packages\n  - camera_start\n  - system_services\nalways_allow: []\n", encoding="utf-8")
    return PolicyEngine(tmp_path / "audit.jsonl", permission_path=permissions), permissions


def test_silent_run_and_approval_run_are_audited(tmp_path):
    policy, _ = _policy(tmp_path)
    assert policy.decide({"name": "system_info"}, {}, "s")["allowed"]
    result = policy.decide({"name": "camera_snapshot"}, {}, "s")
    assert result["status"] == "waiting_approval"
    assert "Hsx wants to:" in result["approval_request"]["summary"]
    assert policy.decide({"name": "camera_snapshot"}, {}, "s", True)["allowed"]
    events = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert [event["decision"] for event in events] == ["allowed", "ask", "approved"]
    assert all("tier" not in event for event in events)


def test_deny_always_allow_restart_and_policy_edit(tmp_path):
    policy, permissions = _policy(tmp_path)
    skill, args = {"name": "capability_resolver"}, {"package": "yt-dlp"}
    assert policy.decide(skill, args, "s", False)["status"] == "cancelled"
    assert policy.decide(skill, args, "s", "always_allow")["status"] == "always_allowed"
    reopened = PolicyEngine(tmp_path / "audit.jsonl", permission_path=permissions)
    assert reopened.decide(skill, args, "s")["status"] == "always_allowed"
    assert "install_packages" in reopened.permission_list()["always_allow"]
    reopened.update_permission("camera_start", False)
    assert "camera_start" not in yaml.safe_load(permissions.read_text())["ask_first"]
    reopened.remove_always_allow("install_packages")
    reopened.reset_always_allow()
    assert reopened.permission_list()["always_allow"] == []


def test_package_install_approval_is_a_plain_vetted_plan(tmp_path):
    policy, _ = _policy(tmp_path)

    decision = policy.decide(
        {"name": "capability_resolver"},
        {"package": "yt-dlp", "capability": "play audio"},
        "music-session",
    )

    request = decision["approval_request"]
    assert decision["status"] == "waiting_approval"
    assert request["request_type"] == "package_install"
    assert "yt-dlp==" in request["summary"]
    assert "about 20 MB" in request["why"]
    assert "virtual environment only" in request["details"]
    assert "No sudo" in request["details"]
    assert "raw" not in str(request).casefold()


def test_hard_deny_cannot_be_approved_and_audit_does_not_leak_values(tmp_path):
    policy, _ = _policy(tmp_path)
    result = policy.decide(
        {"name": "system_execute"},
        {"action": "exec", "command": "echo API_KEY=abc123"},
        "s", True,
    )
    assert result["status"] == "denied"
    serialized = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "abc123" not in serialized
    assert '"hard_deny"' in serialized


def test_always_allow_pattern_is_action_specific(tmp_path):
    policy, permissions = _policy(tmp_path)
    permissions.write_text("ask_first:\n  - camera_*\nalways_allow:\n  - camera_start\n", encoding="utf-8")
    assert policy.decide({"name": "camera_snapshot"}, {}, "s")["status"] == "always_allowed"
    assert policy.decide({"name": "music_play"}, {"query": "song"}, "s")["status"] == "allowed"


def test_web_camera_consent_applies_only_to_its_browser_session(tmp_path, monkeypatch):
    policy, _ = _policy(tmp_path)
    owner = camera_session_owner("browser-one")
    monkeypatch.setattr("agent.hardware.camera.CAMERA.has_owner", lambda candidate: candidate == owner)

    assert policy.decide({"name": "camera_snapshot"}, {}, "browser-one")["allowed"]
    assert policy.decide({"name": "camera_snapshot"}, {}, "browser-two")["status"] == "waiting_approval"


def test_command_classification_handles_quoted_and_base64_commands():
    assert classify_action(
        "system_execute",
        {"action": "exec", "command": "rm -f 'old report.txt'"},
    ) == "delete_owner_files"
    assert classify_action(
        "system_execute",
        {"action": "exec", "command": "base64 -d <<< 'cm0gLWYgL3RtcC9maWxl'"},
    ) == "delete_owner_files"


def test_command_classification_expands_environment_variables(monkeypatch):
    monkeypatch.setenv("HSX_SECRET_PATH", ".env")
    assert classify_action(
        "system_execute",
        {"action": "exec", "command": 'cat "$HSX_SECRET_PATH"'},
    ) == "read_secrets"


def test_write_through_symlink_is_classified_as_overwrite(tmp_path):
    owner_file = tmp_path / "owner.txt"
    owner_file.write_text("owner data", encoding="utf-8")
    link = tmp_path / "owner-link"
    try:
        link.symlink_to(owner_file)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows environment")
    assert classify_action(
        "system_execute",
        {"action": "write_file", "path": str(link)},
    ) == "overwrite_owner_files"
