"""Настройки MCP-сервера.

Все значения читаются из ``.env`` в корне проекта. Если переменная не задана,
используются безопасные значения по умолчанию: сервер слушает только localhost,
а база лежит в ``data/courses.db``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# .env не обязателен для импорта: тесты и запуск с переменными окружения тоже работают.
load_dotenv(BASE_DIR / ".env")


def _str(name: str, default: str = "") -> str:
    """Прочитать строковое значение и убрать лишние пробелы."""
    return (os.getenv(name) or default).strip()


def _int(name: str, default: int) -> int:
    """Прочитать целое число; при мусоре в .env вернуть значение по умолчанию."""
    try:
        return int(_str(name, str(default)))
    except ValueError:
        return default


def _path(name: str, default: Path) -> Path:
    """Прочитать путь и сделать его абсолютным относительно корня проекта."""
    raw = _str(name)
    path = Path(raw).expanduser() if raw else default
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


DEFAULT_REMINDER_TEXT = (
    "Если хотите записаться, напишите имя и удобный контакт — я оформлю заявку, "
    "и менеджер свяжется с вами, чтобы подтвердить детали. "
    "Если пока выбираете, просто задайте вопрос — помогу разобраться."
)


@dataclass(frozen=True)
class ServerSettings:
    """Параметры HTTP-транспорта, расположения базы и напоминаний."""

    host: str
    port: int
    path: str
    db_path: Path
    reminder_delay_seconds: int
    reminder_text: str

    @property
    def url(self) -> str:
        """URL MCP-эндпоинта для локальных клиентов."""
        host = "127.0.0.1" if self.host in {"0.0.0.0", "::"} else self.host
        return f"http://{host}:{self.port}{self.path}"


def load_settings() -> ServerSettings:
    """Собрать настройки сервера из окружения."""
    path = _str("MCP_PATH", "/mcp")
    if not path.startswith("/"):
        path = "/" + path
    return ServerSettings(
        host=_str("MCP_HOST", "127.0.0.1"),
        port=_int("MCP_PORT", 8000),
        path=path,
        db_path=_path("MCP_DB_PATH", BASE_DIR / "data" / "courses.db"),
        reminder_delay_seconds=max(0, _int("REMINDER_DELAY_SECONDS", 120)),
        reminder_text=_str("REMINDER_TEXT", DEFAULT_REMINDER_TEXT),
    )
