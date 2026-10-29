import csv
import html
import io
import statistics
import zipfile
from pathlib import Path

import pdfplumber
from docx import Document
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph as DocxParagraph
from openpyxl import load_workbook
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from agent.workspace import Workspace, task_output_path
from config import WORKSPACE_DIR, WORK_DIR

WORKSPACE = Workspace(WORKSPACE_DIR, WORK_DIR)
MAX_SOURCE_BYTES = 20_000_000
MAX_CELLS = 20_000


def _source_path(workspace: Workspace, filename: str) -> Path:
    source = workspace.resolve_read(filename)
    if not source.is_file():
        raise FileNotFoundError("Workspace file not found.")
    if source.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError(f"Source file exceeds the {MAX_SOURCE_BYTES}-byte limit.")
    return source


def _docx_template(source: Path, relative_path: str) -> dict:
    document = Document(source)
    elements = []
    headings = []
    fonts = set()
    tables = []
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}tbl"):
            table = DocxTable(child, document)
            rows = [[cell.text for cell in row.cells] for row in table.rows]
            tables.append({
                "rows": len(rows),
                "columns": max((len(row) for row in rows), default=0),
                "column_widths_pt": [column.width.pt for column in table.columns if column.width],
                "style": table.style.name if table.style else "",
            })
            elements.append({"kind": "table", "rows": rows})
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        for run in paragraph.runs:
                            if run.font.name:
                                fonts.add(run.font.name)
            continue
        if not child.tag.endswith("}p"):
            continue
        paragraph = DocxParagraph(child, document)
        text = paragraph.text.strip()
        if not text:
            continue
        style = paragraph.style.name if paragraph.style else "Normal"
        sizes = []
        bold = False
        for run in paragraph.runs:
            if run.font.name:
                fonts.add(run.font.name)
            if run.font.size:
                sizes.append(run.font.size.pt)
            bold = bold or bool(run.bold)
        level = int(style.split()[-1]) if style.lower().startswith("heading ") and style.split()[-1].isdigit() else 0
        item = {
            "kind": "heading" if level else "paragraph",
            "heading": text if level else "",
            "text": text,
            "style": style,
            "level": level,
            "font_size_pt": max(sizes) if sizes else None,
            "bold": bold,
        }
        elements.append(item)
        if level:
            headings.append(item)
    sections = []
    for section in document.sections:
        sections.append({
            "page_width_pt": section.page_width.pt,
            "page_height_pt": section.page_height.pt,
            "margins_pt": {
                "left": section.left_margin.pt,
                "right": section.right_margin.pt,
                "top": section.top_margin.pt,
                "bottom": section.bottom_margin.pt,
            },
        })
    headers = [p.text for section in document.sections for p in section.header.paragraphs if p.text.strip()]
    footers = [p.text for section in document.sections for p in section.footer.paragraphs if p.text.strip()]
    for paragraph in [
        p for section in document.sections for p in section.header.paragraphs + section.footer.paragraphs
    ]:
        for run in paragraph.runs:
            if run.font.name:
                fonts.add(run.font.name)
    with zipfile.ZipFile(source) as archive:
        images = [name for name in archive.namelist() if name.startswith("word/media/")]
    return {
        "source_type": "docx",
        "source_path": relative_path,
        "headings": headings,
        "sections": elements,
        "page_sections": sections,
        "fonts": sorted(fonts),
        "headers": headers,
        "footers": footers,
        "tables": tables,
        "images": images,
    }


def _pdf_template(source: Path, relative_path: str) -> dict:
    headings = []
    fonts = set()
    pages = []
    elements = []
    tables = []
    images = []
    for page_number, page in enumerate(pdfplumber.open(source).pages, start=1):
        chars = page.chars
        for char in chars:
            fonts.add(char.get("fontname", ""))
        words = page.extract_words(extra_attrs=["fontname", "size"])
        lines: dict[float, list[dict]] = {}
        for word in words:
            lines.setdefault(round(float(word["top"]), 1), []).append(word)
        sizes = [float(char.get("size", 0)) for char in chars if char.get("size")]
        median_size = statistics.median(sizes) if sizes else 0
        ordered_lines = []
        for top, line_words in sorted(lines.items()):
            text = " ".join(word["text"] for word in sorted(line_words, key=lambda word: float(word["x0"])))
            size = max(float(word.get("size", 0)) for word in line_words)
            font_name = " ".join(str(word.get("fontname", "")) for word in line_words)
            is_heading = size >= median_size * 1.15 or "bold" in font_name.lower()
            item = {
                "kind": "heading" if is_heading else "paragraph",
                "heading": text if is_heading else "",
                "text": text,
                "page": page_number,
                "font_size_pt": size,
                "font": font_name,
                "top_pt": top,
            }
            ordered_lines.append(item)
            elements.append(item)
            if is_heading:
                headings.append(item)
        tables.extend(
            {
                "page": page_number,
                "rows": table,
                "columns": max((len(row) for row in table), default=0),
            }
            for table in (page.extract_tables() or [])
        )
        images.extend(
            {"page": page_number, "x0": item["x0"], "top": item["top"], "x1": item["x1"], "bottom": item["bottom"]}
            for item in page.images
        )
        if chars:
            pages.append({
                "page_width_pt": page.width,
                "page_height_pt": page.height,
                "margins_pt": {
                    "left": min(float(char["x0"]) for char in chars),
                    "right": page.width - max(float(char["x1"]) for char in chars),
                    "top": min(float(char["top"]) for char in chars),
                    "bottom": page.height - max(float(char["bottom"]) for char in chars),
                },
            })
        else:
            pages.append({"page_width_pt": page.width, "page_height_pt": page.height, "margins_pt": {}})
    return {
        "source_type": "pdf",
        "source_path": relative_path,
        "headings": headings,
        "sections": elements,
        "page_sections": pages,
        "fonts": sorted(fonts),
        "headers": [],
        "footers": [],
        "tables": tables,
        "images": images,
    }


def extract_template(workspace: Workspace, filename: str) -> dict:
    source = _source_path(workspace, filename)
    relative_path = source.relative_to(workspace.root).as_posix()
    if source.suffix.lower() == ".docx":
        return _docx_template(source, relative_path)
    if source.suffix.lower() == ".pdf":
        return _pdf_template(source, relative_path)
    raise ValueError("Template extraction supports DOCX and PDF files.")


def read_data(workspace: Workspace, filename: str) -> dict:
    source = _source_path(workspace, filename)
    suffix = source.suffix.lower()
    if suffix == ".csv":
        with source.open(newline="", encoding="utf-8-sig") as file:
            rows = list(csv.reader(file))
        return {"text": "", "tables": [rows[:MAX_CELLS]]}
    if suffix == ".xlsx":
        workbook = load_workbook(source, read_only=True, data_only=True)
        try:
            tables = []
            cell_count = 0
            for sheet in workbook.worksheets:
                rows = []
                for row in sheet.iter_rows(values_only=True):
                    rows.append(list(row))
                    cell_count += len(row)
                    if cell_count >= MAX_CELLS:
                        break
                tables.append({"sheet": sheet.title, "rows": rows})
                if cell_count >= MAX_CELLS:
                    break
        finally:
            workbook.close()
        return {"text": "", "tables": tables}
    if suffix == ".docx":
        document = Document(source)
        text = "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())
        tables = [
            [[cell.text for cell in row.cells] for row in table.rows]
            for table in document.tables
        ]
        return {"text": text[:100_000], "tables": tables}
    if suffix == ".pdf":
        with pdfplumber.open(source) as document:
            text = "\n".join(page.extract_text() or "" for page in document.pages)
            tables = [
                {"page": page_number, "rows": table}
                for page_number, page in enumerate(document.pages, start=1)
                for table in (page.extract_tables() or [])
            ]
        return {"text": text[:100_000], "tables": tables}
    if suffix in {".txt", ".md"}:
        return {"text": source.read_text(encoding="utf-8")[:100_000], "tables": []}
    raise ValueError("Data reading supports XLSX, CSV, DOCX, PDF, TXT, and Markdown files.")


def _content_elements(content) -> tuple[str, list[dict]]:
    if isinstance(content, str):
        return "", [{"heading": "", "body": content}]
    if not isinstance(content, dict):
        raise ValueError("Report content must be text or an object containing sections.")
    title = str(content.get("title", ""))
    sections = content.get("sections", [])
    if not isinstance(sections, list):
        raise ValueError("Report sections must be a list.")
    return title, sections


def make_pdf(workspace: Workspace, structure: dict, content, filename: str) -> dict:
    if not isinstance(structure, dict):
        raise ValueError("A template description is required.")
    title, sections = _content_elements(content)
    output = task_output_path(workspace, filename, title or Path(filename).stem)
    page_data = (structure.get("page_sections") or [{}])[0]
    margins = page_data.get("margins_pt", {})
    page_size = letter
    width = page_data.get("page_width_pt")
    height = page_data.get("page_height_pt")
    if width and height:
        page_size = (float(width), float(height))
    margin_values = {
        name: min(max(float(margins.get(name, 54)), 24), 144)
        for name in ("left", "right", "top", "bottom")
    }
    styles = getSampleStyleSheet()
    template_fonts = " ".join(structure.get("fonts", [])).lower()
    body_font = "Times-Roman" if "times" in template_fonts else "Courier" if "courier" in template_fonts else "Helvetica"
    heading_font = {"Times-Roman": "Times-Bold", "Courier": "Courier-Bold"}.get(body_font, "Helvetica-Bold")
    body_style = ParagraphStyle("HsxBody", parent=styles["BodyText"], fontName=body_font, fontSize=10, leading=14)
    heading_style = ParagraphStyle("HsxHeading", parent=styles["Heading2"], fontName=heading_font, alignment=TA_LEFT)
    title_style = ParagraphStyle("HsxTitle", parent=styles["Title"], fontName=heading_font)

    def report_table(rows):
        if not rows:
            return None
        data = [[Paragraph(html.escape(str(cell if cell is not None else "")), body_style) for cell in row] for row in rows]
        table = Table(data, repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e7e7ef")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ]))
        return table

    story = []
    if title:
        story.extend([Paragraph(html.escape(title), title_style), Spacer(1, 12)])
    for section in sections:
        if not isinstance(section, dict):
            story.append(Paragraph(html.escape(str(section)), body_style))
            continue
        heading = str(section.get("heading", "")).strip()
        body = str(section.get("body", "")).strip()
        if heading:
            story.append(Paragraph(html.escape(heading), heading_style))
            story.append(Spacer(1, 4))
        if body:
            for paragraph in body.splitlines():
                if paragraph.strip():
                    story.append(Paragraph(html.escape(paragraph), body_style))
                    story.append(Spacer(1, 5))
        if section.get("page_break"):
            story.append(PageBreak())
        for rows in section.get("tables", []):
            table = report_table(rows)
            if table:
                story.append(table)
                story.append(Spacer(1, 8))
    for table_rows in content.get("tables", []) if isinstance(content, dict) else []:
        rows = table_rows.get("rows", []) if isinstance(table_rows, dict) else table_rows
        table = report_table(rows)
        if table:
            story.extend([table, Spacer(1, 8)])
    for image_path in content.get("images", []) if isinstance(content, dict) else []:
        image = workspace.resolve_read(image_path)
        if not image.is_file():
            raise FileNotFoundError("A report image was not found inside the workspace.")
        if image.stat().st_size > 5_000_000:
            raise ValueError("Report images must be no larger than 5 MB.")
        story.append(Image(str(image), width=5 * inch, height=3 * inch, kind="proportional"))
    image_names = structure.get("images", [])
    if image_names and structure.get("source_type") == "docx":
        template_path = workspace.resolve_read(structure["source_path"])
        with zipfile.ZipFile(template_path) as archive:
            for image_name in image_names:
                data = archive.read(image_name)
                if len(data) > 5_000_000:
                    raise ValueError("A template image exceeds the 5 MB embedding limit.")
                ImageReader(io.BytesIO(data)).getSize()
                story.append(Image(io.BytesIO(data), width=5 * inch, height=3 * inch, kind="proportional"))
                story.append(Spacer(1, 8))
    elif structure.get("source_type") == "pdf" and structure.get("images"):
        template_path = workspace.resolve_read(structure["source_path"])
        with pdfplumber.open(template_path) as template_pdf:
            for page in template_pdf.pages:
                for image_data in page.images:
                    stream = image_data.get("stream")
                    if stream is None:
                        raise ValueError("A template PDF image could not be extracted.")
                    image_bytes = stream.get_data()
                    if len(image_bytes) > 5_000_000:
                        raise ValueError("A template PDF image exceeds the 5 MB embedding limit.")
                    ImageReader(io.BytesIO(image_bytes)).getSize()
                    story.extend([
                        Image(io.BytesIO(image_bytes), width=5 * inch, height=3 * inch, kind="proportional"),
                        Spacer(1, 8),
                    ])

    def decorate(page_canvas, document):
        page_canvas.saveState()
        if structure.get("headers"):
            page_canvas.setFont("Helvetica", 8)
            page_canvas.drawString(margin_values["left"], page_size[1] - margin_values["top"] + 12, str(structure["headers"][0])[:160])
        if structure.get("footers"):
            page_canvas.setFont("Helvetica", 8)
            page_canvas.drawString(margin_values["left"], margin_values["bottom"] / 2, str(structure["footers"][0])[:160])
        page_canvas.setFont("Helvetica", 8)
        page_canvas.setFillColor(colors.grey)
        page_canvas.drawRightString(page_size[0] - margin_values["right"], margin_values["bottom"] / 2, str(document.page))
        page_canvas.restoreState()

    with output.open("xb") as output_file:
        report = SimpleDocTemplate(
            output_file,
            pagesize=page_size,
            leftMargin=margin_values["left"],
            rightMargin=margin_values["right"],
            topMargin=margin_values["top"],
            bottomMargin=margin_values["bottom"],
            title=title or Path(filename).stem,
        )
        report.build(story, onFirstPage=decorate, onLaterPages=decorate)
    fidelity = (
        "PDF template section order, headings, tables, page size, and margins are approximated; "
        "font metrics and exact pagination are not pixel-perfect."
        if structure.get("source_type") == "pdf"
        else "DOCX headings, section order, tables, margins, and header/footer text were matched approximately; fonts and pagination may differ."
    )
    return {
        "success": True,
        "output_path": str(output),
        "fidelity": fidelity,
    }


def run(
    action: str,
    file: str = "",
    structure: dict | None = None,
    content=None,
    path: str = "report.pdf",
) -> dict:
    if action == "extract_template":
        return {"success": True, "template": extract_template(WORKSPACE, file)}
    if action == "read_data":
        return {"success": True, "data": read_data(WORKSPACE, file)}
    if action == "make_pdf":
        return make_pdf(WORKSPACE, structure or {}, content, path)
    raise ValueError("Action must be extract_template, read_data, or make_pdf.")
