from pathlib import Path

from config import OUTPUTS_DIR


def run(filename: str, html_content: str) -> dict:
    path = OUTPUTS_DIR / f"{Path(filename).stem}.html"
    path.write_text(html_content, encoding="utf-8")
    return {"success": True, "output_path": str(path)}