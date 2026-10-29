from agent.workspace import Workspace, workspace_search
from config import WORKSPACE_DIR, WORK_DIR

WORKSPACE = Workspace(WORKSPACE_DIR, WORK_DIR)


def run(
    action: str,
    path: str = ".",
    query: str = "",
    directory: str = ".",
    file_type: str = "",
    recent_days: int | None = None,
    limit: int = 50,
) -> dict:
    if action == "list":
        return {
            "success": True,
            "files": workspace_search(WORKSPACE, directory=path, limit=limit),
        }
    if action == "search":
        return {
            "success": True,
            "files": workspace_search(
                WORKSPACE,
                query=query,
                directory=directory,
                file_type=file_type,
                recent_days=recent_days,
                limit=limit,
            ),
        }
    if action == "info":
        return {"success": True, "file": WORKSPACE.info(path)}
    if action == "read_text":
        return {"success": True, "path": path, "content": WORKSPACE.read_text(path)}
    raise ValueError("Action must be list, search, info, or read_text.")
