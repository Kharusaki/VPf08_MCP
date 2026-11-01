"""Чтение веб-страниц и извлечение читаемого текста без HTML-разметки."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Dict, List, Tuple

from .http_request import _validate_url
from .http_utils import request

MAX_PAGE_CHARS = 4000
MAX_ALLOWED_CHARS = 8000

_SKIP_TAGS = {
    "script",
    "style",
    "noscript",
    "svg",
    "canvas",
    "iframe",
    "template",
    "form",
    "button",
    "select",
    "option",
    "nav",
    "header",
    "footer",
    "aside",
    "menu",
    "dialog",
}
_BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "li",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "article",
    "section",
    "main",
    "blockquote",
    "pre",
}


class _TextExtractor(HTMLParser):
    """Собирает заголовок и текст страницы, пропуская скрипты и стили."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: List[str] = []
        self.text_parts: List[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag in _BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
            return
        if tag in _BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        else:
            self.text_parts.append(data)


def _normalize(parts: List[str]) -> str:
    text = "".join(parts)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def extract_text(html: str) -> Tuple[str, str]:
    """Вернуть (title, text) для HTML-документа."""
    parser = _TextExtractor()
    parser.feed(html or "")
    parser.close()
    title = _normalize(parser.title_parts)
    text = _normalize(parser.text_parts)
    return title, text


def read_url(url: str, max_chars: int = MAX_PAGE_CHARS) -> Dict[str, object]:
    """Загрузить страницу и вернуть её заголовок и основной текст."""
    url = str(url or "").strip()
    if not url:
        raise ValueError("URL не может быть пустым.")
    max_chars = max(500, min(int(max_chars), MAX_ALLOWED_CHARS))
    _validate_url(url)

    response = request("GET", url, timeout=25, allow_redirects=True)
    _validate_url(response.url)
    if response.status_code >= 400:
        raise RuntimeError(f"Страница вернула HTTP {response.status_code}: {response.url}")

    content_type = response.headers.get("Content-Type", "")
    raw = response.text or ""
    is_html = "html" in content_type.lower() or raw.lstrip().lower().startswith("<!doctype html")

    title = ""
    text = raw
    if is_html:
        title, text = extract_text(raw)
    if not text.strip():
        raise RuntimeError(f"Не удалось извлечь текст со страницы: {response.url}")

    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars].rstrip() + "…"

    return {
        "url": response.url,
        "content_type": content_type,
        "title": title,
        "text": text,
        "truncated": truncated,
    }
