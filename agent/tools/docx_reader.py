"""Чтение текста из документов Word (.docx) без внешних зависимостей."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import List, Optional
from xml.etree import ElementTree as ET

WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _paragraph_text(paragraph: ET.Element) -> str:
    """Собрать текст одного абзаца, включая табуляции и переносы."""
    parts: List[str] = []
    for node in paragraph.iter():
        if node.tag == f"{WORD_NS}t" and node.text:
            parts.append(node.text)
        elif node.tag == f"{WORD_NS}tab":
            parts.append("\t")
        elif node.tag in (f"{WORD_NS}br", f"{WORD_NS}cr"):
            parts.append("\n")
    return "".join(parts)


def _table_lines(table: ET.Element) -> List[str]:
    """Превратить таблицу в строки вида «ячейка | ячейка»."""
    lines: List[str] = []
    for row in table.iter(f"{WORD_NS}tr"):
        cells: List[str] = []
        for cell in row.findall(f"{WORD_NS}tc"):
            cell_text = " ".join(
                text
                for text in (
                    _paragraph_text(paragraph).strip()
                    for paragraph in cell.findall(f"{WORD_NS}p")
                )
                if text
            )
            cells.append(cell_text)
        if any(cells):
            lines.append(" | ".join(cells))
    return lines


def extract_docx_text(path: str | Path, max_chars: Optional[int] = None) -> str:
    """Извлечь текст из .docx: абзацы и таблицы в порядке документа."""
    target = Path(path)
    try:
        with zipfile.ZipFile(target) as archive:
            try:
                document_xml = archive.read("word/document.xml")
            except KeyError as exc:
                raise ValueError("В файле нет word/document.xml — это не .docx.") from exc
    except zipfile.BadZipFile as exc:
        raise ValueError("Не удалось открыть .docx: файл повреждён или это старый .doc.") from exc

    try:
        root = ET.fromstring(document_xml)
    except ET.ParseError as exc:
        raise ValueError("Не удалось разобрать XML внутри .docx.") from exc

    body = root.find(f"{WORD_NS}body")
    if body is None:
        raise ValueError("В .docx не найден раздел body.")

    lines: List[str] = []
    for child in body:
        if child.tag == f"{WORD_NS}p":
            text = _paragraph_text(child).strip()
            if text:
                lines.append(text)
        elif child.tag == f"{WORD_NS}tbl":
            lines.extend(_table_lines(child))

    text = "\n".join(lines)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n\n[... текст обрезан ...]"
    return text
