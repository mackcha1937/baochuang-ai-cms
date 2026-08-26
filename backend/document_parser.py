from __future__ import annotations

from pathlib import Path
from typing import Any


class EmptyDocumentError(ValueError):
    pass


def parse_document(path: Path, extension: str) -> tuple[str, dict[str, Any]]:
    if extension == ".pdf":
        text, structure = _parse_pdf(path)
    elif extension == ".docx":
        text, structure = _parse_docx(path)
    elif extension == ".xlsx":
        text, structure = _parse_xlsx(path)
    else:
        raise ValueError("不支持的文件类型")
    if not text.strip():
        raise EmptyDocumentError("未提取到可用文字，请粘贴原文兜底")
    return text, structure


def _parse_pdf(path: Path) -> tuple[str, dict[str, Any]]:
    from pypdf import PdfReader

    reader = PdfReader(path)
    pages: list[dict[str, Any]] = []
    chunks: list[str] = []
    for number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        pages.append({"page": number, "text": text})
        if text.strip():
            chunks.append(f"[第 {number} 页]\n{text.strip()}")
    return "\n\n".join(chunks), {"kind": "pdf", "pageCount": len(pages), "pages": pages}


def _parse_docx(path: Path) -> tuple[str, dict[str, Any]]:
    from docx import Document

    document = Document(path)
    paragraphs = [
        {"index": index + 1, "text": paragraph.text}
        for index, paragraph in enumerate(document.paragraphs)
        if paragraph.text.strip()
    ]
    tables: list[dict[str, Any]] = []
    for index, table in enumerate(document.tables, start=1):
        rows = [[cell.text for cell in row.cells] for row in table.rows]
        tables.append({"index": index, "rows": rows})
    chunks = [item["text"] for item in paragraphs]
    for table in tables:
        chunks.extend(" | ".join(row) for row in table["rows"] if any(value.strip() for value in row))
    return "\n".join(chunks), {
        "kind": "docx",
        "paragraphCount": len(paragraphs),
        "tableCount": len(tables),
        "paragraphs": paragraphs,
        "tables": tables,
    }


def _parse_xlsx(path: Path) -> tuple[str, dict[str, Any]]:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    sheets: list[dict[str, Any]] = []
    chunks: list[str] = []
    try:
        for worksheet in workbook.worksheets:
            rows: list[list[str]] = []
            for row in worksheet.iter_rows(values_only=True):
                values = ["" if value is None else str(value) for value in row]
                if any(value.strip() for value in values):
                    rows.append(values)
            sheets.append({"name": worksheet.title, "rows": rows})
            if rows:
                chunks.append(f"[工作表：{worksheet.title}]")
                chunks.extend(" | ".join(row) for row in rows)
    finally:
        workbook.close()
    return "\n".join(chunks), {"kind": "xlsx", "sheetCount": len(sheets), "sheets": sheets}
