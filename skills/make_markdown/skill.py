from pathlib import Path

from config import OUTPUTS_DIR


def run(filename: str, title: str, sections: list) -> dict:
    path = OUTPUTS_DIR / f"{Path(filename).stem}.md"
    content = [f"# {title}", ""]
    for section in sections:
        content.extend([f"## {section.get('heading', '')}", section.get('body', ''), ""])
    path.write_text("\n".join(content), encoding="utf-8")
    return {"success": True, "output_path": str(path)}