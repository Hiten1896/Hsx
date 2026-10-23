from pathlib import Path

import pytest

from agent.approval import ApprovalGuard
from agent.devices import DeviceRegistry


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
