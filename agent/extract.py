"""Достаём чистый текст из файла ТЗ: .docx, .pdf, .md, .txt.

Это обычный код, без AI. Заголовки Word превращаем в «# Заголовок»,
пункты списков — в «- пункт», таблицы — в строки «ячейка | ячейка».
"""
from __future__ import annotations

import io
import re
from pathlib import Path

SUPPORTED = (".docx", ".pdf", ".md", ".txt")


def extract_text(data: bytes, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in (".md", ".txt", ""):
        text = _decode(data)
    elif ext == ".docx":
        text = _docx_to_text(data)
    elif ext == ".pdf":
        text = _pdf_to_text(data)
    else:
        raise ValueError(f"Формат {ext} не поддерживается. Загрузите файл .docx, .pdf, .md или .txt.")
    if not text.strip():
        raise ValueError("В файле не нашлось текста.")
    return text


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _docx_to_text(data: bytes) -> str:
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover
        raise ValueError("Для .docx нужна библиотека python-docx: pip install python-docx") from exc

    doc = Document(io.BytesIO(data))
    lines: list[str] = []
    for block in doc.iter_inner_content():
        if hasattr(block, "rows"):  # таблица
            for row in block.rows:
                cells: list[str] = []
                for cell in row.cells:
                    t = " ".join(cell.text.split())
                    if t and (not cells or cells[-1] != t):  # объединённые ячейки повторяются
                        cells.append(t)
                if cells:
                    lines.append(" | ".join(cells))
            lines.append("")
            continue

        text = " ".join(block.text.split())
        if not text:
            lines.append("")
            continue
        style = (block.style.name or "").lower() if block.style is not None else ""
        level = _heading_level(style)
        if level:
            lines += ["", "#" * level + " " + text, ""]
        elif "list" in style or "спис" in style or _has_numbering(block):
            lines.append("- " + text)
        else:
            lines += [text, ""]
    return "\n".join(lines)


def _heading_level(style: str) -> int:
    if style in ("title", "название"):
        return 1
    if style.startswith("heading") or style.startswith("заголовок"):
        digits = re.findall(r"\d+", style)
        return min(int(digits[0]), 6) if digits else 1
    return 0


def _has_numbering(paragraph) -> bool:
    ppr = paragraph._p.pPr
    return ppr is not None and ppr.numPr is not None


def _pdf_to_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise ValueError("Для .pdf нужна библиотека pypdf: pip install pypdf") from exc
    reader = PdfReader(io.BytesIO(data))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    if not text.strip():
        raise ValueError("В PDF нет текстового слоя (похоже на скан). Загрузите .docx или вставьте текст.")
    return text
