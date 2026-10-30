"""Инструменты «Файлы»: чтение, запись и просмотр папки.

Все операции разрешены только внутри папки workspace/.
Это защищает проект от случайной записи в системные файлы.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from ..config import SETTINGS

logger = logging.getLogger(__name__)


def _workspace() -> Path:
    """Актуальный путь песочницы (может меняться в тестах)."""
    root = SETTINGS.workspace_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve(path: str) -> Path:
    """Превратить пользовательский путь в безопасный абсолютный путь."""
    root = _workspace()
    raw = (path or ".").strip()
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()

    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("Можно работать только с файлами внутри папки workspace/.") from exc
    return resolved


def _relative(path: Path) -> str:
    """Путь относительно workspace/ — его удобно показывать пользователю."""
    try:
        return str(path.relative_to(_workspace()))
    except ValueError:
        return str(path)


def read_file(path: str, max_bytes: int | None = None) -> str:
    """Прочитать текстовый файл из workspace/."""
    target = _resolve(path)
    if not target.exists():
        raise FileNotFoundError(f"Файл не найден: {_relative(target)}")
    if not target.is_file():
        raise IsADirectoryError(f"Это не файл: {_relative(target)}")

    limit = max_bytes or SETTINGS.max_read_bytes
    size = target.stat().st_size
    if size > limit:
        raise ValueError(
            f"Файл слишком большой ({size} байт). Лимит: {limit} байт. "
            "Укажите max_bytes или используйте файл поменьше."
        )

    return target.read_text(encoding="utf-8", errors="replace")


def write_file(path: str, content: str, append: bool = False) -> str:
    """Записать текст в файл внутри workspace/ (можно дописать в конец)."""
    target = _resolve(path)
    if target == _workspace():
        raise ValueError("Нужно указать имя файла, а не только папку.")
    if not isinstance(content, str):
        content = str(content)

    data = content.encode("utf-8")
    if len(data) > SETTINGS.max_write_bytes:
        raise ValueError(
            f"Слишком большой текст ({len(data)} байт). "
            f"Лимит: {SETTINGS.max_write_bytes} байт."
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with open(target, mode, encoding="utf-8") as f:
        f.write(content)

    logger.info("Записан файл %s (append=%s)", target, append)
    return _relative(target)


def list_directory(path: str = ".") -> List[Dict[str, Any]]:
    """Показать содержимое папки внутри workspace/."""
    target = _resolve(path)
    if not target.exists():
        raise FileNotFoundError(f"Папка не найдена: {_relative(target)}")
    if not target.is_dir():
        raise NotADirectoryError(f"Это не папка: {_relative(target)}")

    items: List[Dict[str, Any]] = []
    for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:200]:
        try:
            stat = item.stat()
            modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            modified = ""
            stat = None
        items.append(
            {
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
                "size": stat.st_size if stat and item.is_file() else None,
                "modified": modified,
            }
        )
    return items
