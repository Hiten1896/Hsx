from pathlib import Path

from docx import Document

from config import OUTPUTS_DIR
from agent.security import safe_path


def run(filename: str, title: str, sections: list) -> dict:
    path = safe_path(OUTPUTS_DIR, f"{Path(filename).stem}.docx")
    document = Document()
    document.add_heading(title, 0)
    for section in sections:
        document.add_heading(section.get("heading", ""), level=1)
        document.add_paragraph(section.get("body", ""))
    document.save(path)
    return {"success": True, "output_path": str(path)}