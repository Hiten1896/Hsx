from agent.security import safe_path
from config import OUTPUTS_DIR, UPLOADS_DIR


def run(path: str) -> dict:
    for base in (UPLOADS_DIR, OUTPUTS_DIR):
        try:
            target = safe_path(base, path)
        except ValueError:
            continue
        if target.is_file():
            return {"success": True, "path": str(target), "content": target.read_text(encoding="utf-8")[:100_000]}
    return {"success": False, "error": "File not found in uploads or outputs."}
