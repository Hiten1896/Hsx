from pathlib import Path

from config import OUTPUTS_DIR
from agent.security import safe_path


def run(filename: str, title: str, sections: list) -> dict:
    path = safe_path(OUTPUTS_DIR, f"{Path(filename).stem}.md")
    content = [f"# {title}", ""]
    for section in sections:
        content.extend([f"## {section.get('heading', '')}", section.get('body', ''), ""])
    path.write_text("\n".join(content), encoding="utf-8")
    return {"success": True, "output_path": str(path)}