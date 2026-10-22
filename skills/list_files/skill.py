from agent.security import safe_path
from config import OUTPUTS_DIR, UPLOADS_DIR


def run(directory: str = "uploads") -> dict:
    base = UPLOADS_DIR if directory == "uploads" else OUTPUTS_DIR if directory == "outputs" else None
    if base is None:
        return {"success": False, "error": "Directory must be uploads or outputs."}
    target = safe_path(base, ".")
    return {"success": True, "files": [p.name for p in target.iterdir() if p.is_file()]}
