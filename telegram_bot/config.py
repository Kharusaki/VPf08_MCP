"""Настройки Telegram-бота и клиента MCP.

Секреты лежат в ``.env`` в корне проекта. Здесь они один раз превращаются в
удобный неизменяемый объект, чтобы остальной код не читал окружение напрямую.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _str(name: str, default: str = "") -> str:
    """Прочитать строку из окружения без лишних пробелов."""
    return (os.getenv(name) or default).strip()


def _int(name: str, default: int) -> int:
    """Прочитать целое число; при ошибке вернуть default."""
    try:
        return int(_str(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    """Прочитать дробное число; при ошибке вернуть default."""
    try:
        return float(_str(name, str(default)))
    except ValueError:
        return default


def _path(name: str, default: Path) -> Path:
    """Прочитать путь и сделать его абсолютным относительно корня проекта."""
    raw = _str(name)
    path = Path(raw).expanduser() if raw else default
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


@dataclass(frozen=True)
class Settings:
    """Все параметры бота в одном месте."""

    # Telegram
    bot_token: str
    telegram_proxy: str

    # LLM (OpenAI-совместимый API)
    openai_api_key: str
    openai_base_url: str
    default_model: str
    fallback_model: str
    temperature: float
    max_tokens: int
    request_timeout: int

    # MCP
    mcp_url: str
    mcp_timeout: int
    mcp_read_timeout: int
    max_tool_rounds: int
    max_tool_result_chars: int

    # История диалога
    history_file: Path
    history_limit: int
    history_max_chars: int
    max_message_chars: int

    log_file: Path

    def missing_required(self) -> list[str]:
        """Вернуть имена обязательных переменных, которых не хватает."""
        missing: list[str] = []
        if not self.bot_token:
            missing.append("BOT_TOKEN")
        if not self.openai_api_key:
            missing.append("OPENAI_API_KEY")
        return missing


def load_settings() -> Settings:
    """Собрать настройки из ``.env`` и переменных окружения."""
    host = _str("MCP_HOST", "127.0.0.1")
    port = _int("MCP_PORT", 8000)
    client_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    mcp_path = _str("MCP_PATH", "/mcp")
    if not mcp_path.startswith("/"):
        mcp_path = "/" + mcp_path
    default_mcp_url = f"http://{client_host}:{port}{mcp_path}"

    return Settings(
        bot_token=_str("BOT_TOKEN"),
        telegram_proxy=_str("TELEGRAM_PROXY"),
        openai_api_key=_str("OPENAI_API_KEY"),
        openai_base_url=_str("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        default_model=_str("DEFAULT_MODEL", "openai/gpt-4.1-mini"),
        fallback_model=_str("FALLBACK_MODEL"),
        temperature=_float("DEFAULT_TEMPERATURE", 0.3),
        max_tokens=_int("DEFAULT_MAX_TOKENS", 650),
        request_timeout=_int("REQUEST_TIMEOUT", 60),
        mcp_url=_str("MCP_URL", default_mcp_url),
        mcp_timeout=_int("MCP_TIMEOUT", 30),
        mcp_read_timeout=_int("MCP_READ_TIMEOUT", 60),
        max_tool_rounds=_int("MAX_TOOL_ROUNDS", 5),
        max_tool_result_chars=_int("MAX_TOOL_RESULT_CHARS", 5000),
        history_file=_path("HISTORY_FILE", BASE_DIR / "data" / "history.json"),
        history_limit=_int("HISTORY_LIMIT", 12),
        history_max_chars=_int("HISTORY_MAX_CHARS", 9000),
        max_message_chars=_int("MAX_MESSAGE_CHARS", 4000),
        log_file=_path("LOG_FILE", BASE_DIR / "data" / "bot.log"),
    )
