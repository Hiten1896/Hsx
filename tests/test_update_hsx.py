import subprocess
import json
from pathlib import Path

import pytest

from scripts import update_hsx


def test_automatic_updates_are_disabled_by_default():
    assert update_hsx.update_once({}) is False


def test_update_requires_a_pinned_signing_fingerprint(monkeypatch):
    def unexpected_git(*args, **kwargs):
        raise AssertionError("Git must not run without a signer fingerprint.")

    monkeypatch.setattr(update_hsx, "_git", unexpected_git)
    with pytest.raises(RuntimeError, match="HSX_UPDATE_SIGNER_FINGERPRINT"):
        update_hsx.update_once({"HSX_AUTO_UPDATE": "1"})


def test_valid_signer_fingerprint_is_extracted_from_gpg_status():
    result = update_hsx._valid_signer_fingerprint(
        "[GNUPG:] VALIDSIG 0123456789ABCDEF0123456789ABCDEF01234567 2026-10-30"
    )

    assert result == "0123456789ABCDEF0123456789ABCDEF01234567"
    assert update_hsx._valid_signer_fingerprint("[GNUPG:] BADSIG") is None


def test_requirement_detection_only_accepts_root_level_requirements_files():
    assert update_hsx._changed_requirement_files(
        ["requirements.txt", "requirements-voice.txt", "skills/example/requirements.txt", "../requirements.txt"]
    ) == ["requirements-voice.txt", "requirements.txt"]


def test_node_dependency_changes_are_detected_from_manifests_only():
    assert update_hsx._has_node_dependency_changes(["package.json"])
    assert update_hsx._has_node_dependency_changes(["package-lock.json"])
    assert not update_hsx._has_node_dependency_changes(["web/app.js"])


def test_untrusted_signature_is_rejected(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        update_hsx,
        "_git",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="", stderr="[GNUPG:] VALIDSIG FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF"
        ),
    )

    with pytest.raises(RuntimeError, match="not the configured signer"):
        update_hsx._verify_commit(tmp_path, "deadbeef", "0123456789ABCDEF0123456789ABCDEF01234567")


def test_requirements_install_fails_explicitly_when_virtualenv_is_missing(tmp_path: Path):
    with pytest.raises(RuntimeError, match="virtual environment Python was not found"):
        update_hsx._install_requirements(tmp_path, tmp_path / "venv", ["requirements.txt"])


def test_failed_dependency_update_is_retried_before_reload(tmp_path: Path, monkeypatch):
    pending = tmp_path / "pending.json"
    pending.write_text(
        json.dumps({"commit": "abc123", "requirements": ["requirements.txt"], "node_dependencies": False}),
        encoding="utf-8",
    )
    monkeypatch.setattr(update_hsx, "_pending_file", lambda _repo: pending)
    attempts = 0

    def install(_repo, _venv, _files):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary dependency failure")

    monkeypatch.setattr(update_hsx, "_install_requirements", install)
    with pytest.raises(RuntimeError, match="temporary dependency failure"):
        update_hsx._finish_pending_dependencies(tmp_path, tmp_path / "venv")
    assert pending.exists()
    assert not update_hsx._reload_marker(tmp_path).exists()

    assert update_hsx._finish_pending_dependencies(tmp_path, tmp_path / "venv") is True
    assert not pending.exists()
    assert update_hsx._reload_marker(tmp_path).exists()
