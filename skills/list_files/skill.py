from agent.workspace import Workspace, workspace_search
from config import WORKSPACE_DIR, WORK_DIR

WORKSPACE = Workspace(WORKSPACE_DIR, WORK_DIR)


def run(directory: str = ".") -> dict:
    try:
        files = workspace_search(WORKSPACE, directory=directory, limit=200)
    except (OSError, ValueError) as error:
        return {"success": False, "error": str(error)}
    return {
        "success": True,
        "files": [item["path"] for item in files],
        "truncated": len(files) == 200,
    }
