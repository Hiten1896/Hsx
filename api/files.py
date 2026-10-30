from __future__ import annotations

from pathlib import Path


def resolve_output(workspace, relative_path: str) -> Path:
    output = workspace.resolve_write(relative_path)
    if not output.is_file():
        raise FileNotFoundError("Artifact not found.")
    return output


def list_output_files(workspace, work_dir: Path) -> list[dict]:
    return [
        {"name": item["name"], "path": item["path"], "type": item["type"]}
        for item in workspace_search(workspace, directory=work_dir.relative_to(workspace.root), limit=200)
    ]


def workspace_search(workspace, **kwargs):
    from agent.workspace import workspace_search as search

    return search(workspace, **kwargs)
