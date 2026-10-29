from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from agent.workspace import Workspace, task_output_path
from config import WORKSPACE_DIR, WORK_DIR

WORKSPACE = Workspace(WORKSPACE_DIR, WORK_DIR)


def run(filenames: list, zip_name: str) -> dict:
    archive = task_output_path(WORKSPACE, f"{Path(zip_name).stem}.zip")
    with ZipFile(archive, "x", ZIP_DEFLATED) as zip_file:
        for filename in filenames:
            source = WORKSPACE.resolve_read(filename)
            if source.exists():
                zip_file.write(source, source.relative_to(WORKSPACE.root).as_posix())
    return {"success": True, "output_path": str(archive)}