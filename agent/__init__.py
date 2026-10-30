"""Пакет AI-агента.

Здесь лежит всё "мозги" проекта:
    config.py   — чтение настроек из .env
    memory.py   — долговременная память (memory.json)
    tools/      — инструменты (погода, крипта, поиск, файлы и т.д.)
    agent.py    — агентный цикл (LLM сама выбирает инструменты)
    bot.py      — Telegram-бот
"""

from .config import SETTINGS

__all__ = ["SETTINGS"]
__version__ = "1.0.0"
