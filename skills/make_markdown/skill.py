from pathlib import Path

from agent.workspace import Workspace, task_output_path
from config import WORKSPACE_DIR, WORK_DIR

WORKSPACE = Workspace(WORKSPACE_DIR, WORK_DIR)


def run(filename: str, title: str, sections: list) -> dict:
    path = task_output_path(WORKSPACE, f"{Path(filename).stem}.md")
    content = [f"# {title}", ""]
    for section in sections:
        content.extend([f"## {section.get('heading', '')}", section.get('body', ''), ""])
    with path.open("x", encoding="utf-8") as output_file:
        output_file.write("\n".join(content))
    return {"success": True, "output_path": str(path)}