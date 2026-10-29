from pathlib import Path

from agent.workspace import Workspace, task_output_path
from config import WORKSPACE_DIR, WORK_DIR

WORKSPACE = Workspace(WORKSPACE_DIR, WORK_DIR)


def run(filename: str, html_content: str) -> dict:
    path = task_output_path(WORKSPACE, f"{Path(filename).stem}.html")
    with path.open("x", encoding="utf-8") as output_file:
        output_file.write(html_content)
    return {"success": True, "output_path": str(path)}