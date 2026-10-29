from agent.workspace import Workspace
from config import WORKSPACE_DIR, WORK_DIR

WORKSPACE = Workspace(WORKSPACE_DIR, WORK_DIR)


def run(path: str) -> dict:
    try:
        target = WORKSPACE.resolve_read(path)
        return {
            "success": True,
            "path": target.relative_to(WORKSPACE.root).as_posix(),
            "content": WORKSPACE.read_text(path),
        }
    except (OSError, ValueError) as error:
        return {"success": False, "error": str(error)}
