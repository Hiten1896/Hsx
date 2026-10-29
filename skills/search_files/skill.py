from agent.workspace import Workspace
from config import WORKSPACE_DIR, WORK_DIR

WORKSPACE = Workspace(WORKSPACE_DIR, WORK_DIR)


def run(query: str, directory: str = ".") -> dict:
    matches = []
    try:
        for path in WORKSPACE.iter_files(directory):
            try:
                content = WORKSPACE.read_text(path.relative_to(WORKSPACE.root))
            except (OSError, ValueError, UnicodeDecodeError):
                continue
            if query.lower() in content.lower():
                matches.append(path.relative_to(WORKSPACE.root).as_posix())
    except (OSError, ValueError) as error:
        return {"success": False, "error": str(error)}
    return {"success": True, "matches": matches[:100]}
