from pathlib import Path

import pytest

from agent.approval import ApprovalGuard
from agent.devices import DeviceRegistry, resolve_target_device, system_body_context, system_body_state


def test_device_pair_authorize_and_revoke(tmp_path: Path):
    pin = "owner-pin"
    audit = tmp_path / "device_audit.jsonl"
    registry = DeviceRegistry(tmp_path / "devices.db", ApprovalGuard(tmp_path / "owner.pin", pin), audit)
    paired = registry.pair("android-main", "Phone", "android", ["status.read", "notify.send"], pin)
    assert paired["token"]
    assert registry.authorize("android-main", paired["token"], "status.read")["authorized"]
    with pytest.raises(PermissionError):
        registry.authorize("android-main", paired["token"], "camera.capture")
    registry.revoke("android-main", pin)
    with pytest.raises(PermissionError):
        registry.authorize("android-main", paired["token"], "status.read")
    assert audit.exists()
    assert all("token" not in line for line in audit.read_text(encoding="utf-8").splitlines())


def test_pairing_rejects_unapproved_capability(tmp_path: Path):
    registry = DeviceRegistry(tmp_path / "devices.db", ApprovalGuard(tmp_path / "owner.pin", "pin"))
    with pytest.raises(ValueError):
        registry.pair("lan-1", "Router", "local_network", ["shell.exec"], "pin")


def test_local_host_routing_is_limited_to_loopback():
    assert resolve_target_device("local_host", "127.0.0.1") == "local_host"
    assert resolve_target_device("local_host", "::ffff:127.0.0.1") == "local_host"
    assert resolve_target_device("client_browser", "203.0.113.10") == "client_browser"
    with pytest.raises(PermissionError):
        resolve_target_device("local_host", "203.0.113.10")
    with pytest.raises(ValueError):
        resolve_target_device("unknown", "127.0.0.1")


def test_system_body_context_discloses_permissions_and_hardware_roles():
    state = system_body_state("local_host")
    context = system_body_context("local_host")
    assert state["target_device"] == "local_host"
    assert state["interfaces"]["microphone"]["role"].startswith("ears")
    assert state["interfaces"]["webcam"]["role"].startswith("eyes")
    assert "service account" in context
    assert "not sentience" in context
