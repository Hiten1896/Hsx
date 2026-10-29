import os
from pathlib import Path

import pytest

from agent.workspace import Workspace, workspace_search


def test_workspace_read_jail_rejects_traversal_absolute_and_symlink_escape(tmp_path: Path):
    root = tmp_path / "share"
    work = root / "Hsx Work"
    work.mkdir(parents=True)
    (root / "source.txt").write_text("source", encoding="utf-8")
    workspace = Workspace(root, "Hsx Work")

    assert workspace.resolve_read("source.txt").read_text(encoding="utf-8") == "source"
    with pytest.raises(ValueError):
        workspace.resolve_read("../outside.txt")
    with pytest.raises(ValueError):
        workspace.resolve_read(str(tmp_path / "outside.txt"))

    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows environment")
    with pytest.raises(ValueError):
        workspace.resolve_read("escape/secret.txt")
    assert [path.name for path in workspace.files()] == ["source.txt"]


def test_workspace_writes_only_in_work_dir_and_uses_collision_suffixes(tmp_path: Path):
    root = tmp_path / "share"
    work = root / "Hsx Work"
    work.mkdir(parents=True)
    (root / "report.pdf").write_text("owner file", encoding="utf-8")
    (work / "report.pdf").write_text("first result", encoding="utf-8")
    workspace = Workspace(root, "Hsx Work")

    result = workspace.unique_write_path("report.pdf")
    assert result == work / "report (1).pdf"
    assert (root / "report.pdf").read_text(encoding="utf-8") == "owner file"
    with pytest.raises(ValueError):
        workspace.resolve_write("../report.pdf")


def test_workspace_work_dir_cannot_be_a_symlink(tmp_path: Path):
    root = tmp_path / "share"
    root.mkdir()
    (root / "other").mkdir()
    with pytest.raises(ValueError):
        Workspace(root, ".")
    try:
        (root / "Hsx Work").symlink_to(root / "other", target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows environment")
    with pytest.raises(ValueError):
        Workspace(root, "Hsx Work")


def test_workspace_search_tolerates_typos_and_filters_by_type_and_age(tmp_path: Path):
    root = tmp_path / "share"
    root.mkdir()
    (root / "Quarterly Report.docx").write_text("x", encoding="utf-8")
    old_photo = root / "photo.jpg"
    old_photo.write_text("x", encoding="utf-8")
    old_time = old_photo.stat().st_mtime - 10 * 86400
    os.utime(old_photo, (old_time, old_time))
    workspace = Workspace(root, "Hsx Work")

    matches = workspace_search(workspace, query="quaterly reort", file_type="docx")
    assert [item["name"] for item in matches] == ["Quarterly Report.docx"]
    assert workspace_search(workspace, query="", file_type="pdf") == []
    assert [item["name"] for item in workspace_search(workspace, recent_days=1)] == [
        "Quarterly Report.docx"
    ]


def test_legacy_file_listing_uses_workspace_with_a_bounded_result(tmp_path: Path, monkeypatch):
    from skills.list_files import skill

    root = tmp_path / "share"
    root.mkdir()
    for index in range(205):
        (root / f"file-{index}.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(skill, "WORKSPACE", Workspace(root, "Hsx Work"))

    result = skill.run()

    assert result["success"]
    assert len(result["files"]) == 200
    assert result["truncated"]
