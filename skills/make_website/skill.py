from pathlib import Path

from config import OUTPUTS_DIR
from agent.security import safe_path


def run(filename: str, html_content: str) -> dict:
    path = safe_path(OUTPUTS_DIR, f"{Path(filename).stem}.html")
    path.write_text(html_content, encoding="utf-8")
    return {"success": True, "output_path": str(path)}