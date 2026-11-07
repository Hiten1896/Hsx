from pathlib import Path
from types import SimpleNamespace

import pytest

from agent import system_tools


def test_system_info_reports_local_machine(monkeypatch):
    calls = []

    def fake_disk_usage(path):
        calls.append(Path(path))
        if Path(path) == system_tools.WORKSPACE_DIR:
            return SimpleNamespace(total=400, used=100, free=300)
        return SimpleNamespace(total=33, used=10, free=23)

    monkeypatch.setattr(
        system_tools.psutil, "cpu_count", lambda logical=True: 2 if logical else 1
    )
    monkeypatch.setattr(system_tools.psutil, "disk_usage", fake_disk_usage)
    info = system_tools.system_info()
    assert info["hostname"]
    assert info["cpu"]["logical_cores"] == 2
    assert info["memory"]["total_bytes"] > 0
    assert info["os"]["system"]
    assert info["local_time"]
    assert info["disk"]["total_bytes"] == 400
    assert info["disk"]["free_bytes"] == 300
    assert info["system_disk"]["total_bytes"] == 33
    assert system_tools.WORKSPACE_DIR in calls


def test_create_text_file_uses_numeric_suffix_and_stays_in_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(system_tools, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(system_tools, "WORK_DIR", tmp_path / "Hsx Work")
    first = system_tools.create_text_file("Reports/summary.txt", "one")
    second = system_tools.create_text_file("Reports/summary.txt", "two")

    assert Path(first["path"]).read_text() == "one"
    assert Path(second["path"]).name == "summary (1).txt"
    assert Path(second["path"]).read_text() == "two"
    with pytest.raises(ValueError, match="workspace"):
        system_tools.create_text_file("../outside.txt", "no")


def test_create_text_file_rejects_symlink_escape(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    try:
        (workspace / "escape").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Symlink creation is not permitted: {error}")
    monkeypatch.setattr(system_tools, "WORKSPACE_DIR", workspace)
    monkeypatch.setattr(system_tools, "WORK_DIR", workspace / "Hsx Work")

    with pytest.raises(ValueError, match="workspace"):
        system_tools.create_text_file("escape/file.txt", "no")
