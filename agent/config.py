"""Загрузка настроек из файла .env.

Все "секреты" (токены, ключи) и параметры лежат в .env в корне проекта.
Здесь мы один раз читаем их в удобный объект SETTINGS и раздаём остальному коду.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Корень проекта = папка, в которой лежит папка agent/
BASE_DIR = Path(__file__).resolve().parent.parent

# Загружаем .env из корня проекта (если файла нет — не страшно, возьмём значения по умолчанию)
load_dotenv(BASE_DIR / ".env")


def _str(name: str, default: str = "") -> str:
    """Прочитать строку из .env."""
    return (os.getenv(name) or default).strip()


def _int(name: str, default: int) -> int:
    """Прочитать целое число из .env (если там мусор — вернём default)."""
    try:
        return int((os.getenv(name) or "").strip())
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    """Прочитать дробное число из .env."""
    try:
        return float((os.getenv(name) or "").strip())
    except ValueError:
        return default


def _path(name: str, default: str) -> Path:
    """Прочитать путь из .env и сделать его абсолютным."""
    value = _str(name, default)
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


@dataclass
class Settings:
    """Все настройки агента в одном месте."""

    # --- Доступ к LLM (OpenAI-совместимый API) ---
    api_key: str
    base_url: str
    default_model: str
    fallback_model: str
    temperature: float
    max_tokens: int
    request_timeout: int

    # --- Telegram ---
    bot_token: str
    telegram_proxy: str

    # --- Прокси для обычных HTTP-запросов (погода, крипта, поиск) ---
    # Если пусто, но задан TELEGRAM_PROXY — используем его (удобно за NAT/VPN).
    http_proxy: str

    # --- Память и история ---
    history_limit: int
    memory_file: Path
    reminders_file: Path

    # --- Файловая песочница: агент читает/пишет только внутри этой папки ---
    workspace_dir: Path

    # --- Прочее ---
    system_prompt: str
    log_file: Path

    # Сколько раз за один вопрос агент может вызвать инструменты (защита от бесконечного цикла)
    max_tool_rounds: int = 8

    # Как часто бот проверяет напоминания (в секундах)
    reminder_check_interval: int = 30

    # Ограничения на размеры данных
    max_read_bytes: int = 200_000
    max_write_bytes: int = 1_000_000
    max_tool_result_chars: int = 6_000


def _load_settings() -> Settings:
    http_proxy = _str("HTTP_PROXY_URL") or _str("TELEGRAM_PROXY")

    settings = Settings(
        api_key=_str("OPENAI_API_KEY"),
        base_url=_str("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        default_model=_str("DEFAULT_MODEL", "gpt-4o-mini"),
        fallback_model=_str("FALLBACK_MODEL", ""),
        temperature=_float("DEFAULT_TEMPERATURE", 0.7),
        max_tokens=_int("DEFAULT_MAX_TOKENS", 800),
        request_timeout=_int("REQUEST_TIMEOUT", 60),
        bot_token=_str("BOT_TOKEN"),
        telegram_proxy=_str("TELEGRAM_PROXY"),
        http_proxy=http_proxy,
        history_limit=_int("HISTORY_LIMIT", 20),
        memory_file=_path("MEMORY_FILE", str(BASE_DIR / "memory.json")),
        reminders_file=_path("REMINDERS_FILE", str(BASE_DIR / "reminders.json")),
        workspace_dir=_path("WORKSPACE_DIR", str(BASE_DIR / "workspace")),
        system_prompt=_str(
            "SYSTEM_PROMPT",
            "Ты дружелюбный и полезный ассистент. Отвечай на языке пользователя, кратко и по делу.",
        ),
        log_file=_path("LOG_FILE", str(BASE_DIR / "bot.log")),
        max_tool_rounds=_int("MAX_TOOL_ROUNDS", 8),
        reminder_check_interval=_int("REMINDER_CHECK_INTERVAL", 30),
    )

    # Папки, которые агент создаёт сам, чтобы у новичка ничего не падало
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    (settings.workspace_dir / ".gitkeep").touch(exist_ok=True)

    return settings


# Единый экземпляр настроек на весь проект
SETTINGS = _load_settings()
