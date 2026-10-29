import csv
from pathlib import Path

from agent.workspace import Workspace, task_output_path
from config import WORKSPACE_DIR, WORK_DIR

WORKSPACE = Workspace(WORKSPACE_DIR, WORK_DIR)


def run(filename: str, headers: list, rows: list) -> dict:
    path = task_output_path(WORKSPACE, f"{Path(filename).stem}.csv")
    with path.open("x", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(headers)
        writer.writerows(rows)
    return {"success": True, "output_path": str(path)}