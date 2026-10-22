from config import OUTPUTS_DIR, UPLOADS_DIR


def run(query: str, directory: str = "uploads") -> dict:
    base = UPLOADS_DIR if directory == "uploads" else OUTPUTS_DIR if directory == "outputs" else None
    if base is None:
        return {"success": False, "error": "Directory must be uploads or outputs."}
    matches = []
    for path in base.rglob("*"):
        if path.is_file():
            try:
                if query.lower() in path.read_text(encoding="utf-8").lower():
                    matches.append(path.name)
            except (OSError, UnicodeDecodeError):
                continue
    return {"success": True, "matches": matches[:100]}
