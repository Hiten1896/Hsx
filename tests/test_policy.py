import json

import pytest

from agent.autonomy import classify_action
from agent.policy import PolicyEngine


def _policy(tmp_path):
    return PolicyEngine(tmp_path / "audit.jsonl")


def test_silent_run_and_approval_run_are_audited(tmp_path):
    policy = _policy(tmp_path)
    result = policy.decide({"name": "system_info"}, {}, "s")
    assert result["status"] == "waiting_approval"
    assert result["approval_request"]["request_type"] == "tool_consent"
    assert "Possible consequence:" in result["approval_request"]["why"]
    assert policy.decide({"name": "system_info"}, {}, "s", True)["allowed"]
    result = policy.decide({"name": "camera_snapshot"}, {}, "s")
    assert result["status"] == "waiting_approval"
    assert "camera" in result["approval_request"]["summary"]
    assert "camera feed" in result["approval_request"]["why"]
    assert policy.decide({"name": "camera_snapshot"}, {}, "s", True)["allowed"]
    events = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert [event["decision"] for event in events] == ["ask", "approved", "ask", "approved"]
    assert all("tier" not in event for event in events)


def test_approval_is_one_time_and_ignores_legacy_persistent_grants(tmp_path):
    policy = _policy(tmp_path)
    skill, args = {"name": "capability_resolver"}, {"package": "yt-dlp"}
    assert policy.decide(skill, args, "s", False)["status"] == "cancelled"
    assert policy.decide(skill, args, "s", "always_allow")["status"] == "waiting_approval"
    assert policy.decide(skill, args, "s", True)["status"] == "approved"
    assert policy.decide(skill, args, "s")["status"] == "waiting_approval"


def test_package_install_approval_is_a_plain_vetted_plan(tmp_path):
    policy = _policy(tmp_path)

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


def test_esptool_install_uses_the_same_explicit_approval_card(tmp_path):
    policy = _policy(tmp_path)

    decision = policy.decide(
        {"name": "capability_resolver"},
        {"package": "esptool", "capability": "inspect an ESP32-S3"},
        "esp32-session",
    )

    request = decision["approval_request"]
    assert decision["status"] == "waiting_approval"
    assert request["request_type"] == "package_install"
    assert "esptool==5.6.0" in request["summary"]
    assert "virtual environment only" in request["details"]
    assert "No sudo" in request["details"]


def test_firmware_flash_requires_explicit_one_time_approval(tmp_path):
    policy = _policy(tmp_path)
    skill = {"name": "system_execute"}
    arguments = {
        "action": "exec",
        "command": "python -m esptool --port COM5 write-flash 0x0 firmware.bin",
    }

    pending = policy.decide(skill, arguments, "esp32-session")
    assert pending["status"] == "waiting_approval"
    assert pending["approval_request"]["request_type"] == "firmware_flash"

    legacy_grant = policy.decide(skill, arguments, "esp32-session", "always_allow")
    assert legacy_grant["status"] == "waiting_approval"
    approved = policy.decide(skill, arguments, "esp32-session", True)
    assert approved == {"allowed": True, "status": "approved"}
    assert policy.decide(skill, arguments, "esp32-session")["status"] == "waiting_approval"


def test_hard_deny_cannot_be_approved_and_audit_does_not_leak_values(tmp_path):
    policy = _policy(tmp_path)
    result = policy.decide(
        {"name": "system_execute"},
        {"action": "exec", "command": "echo API_KEY=abc123"},
        "s", True,
    )
    assert result["status"] == "denied"
    serialized = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "abc123" not in serialized
    assert '"hard_deny"' in serialized


def test_ask_user_is_exempt_from_action_approval(tmp_path):
    policy = _policy(tmp_path)
    assert policy.decide({"name": "ask_user"}, {"question": "Which file?"}, "s")["allowed"]


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
