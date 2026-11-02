"""Дымовой тест реальной связки MCP-сервер + LLM (без Telegram).

Запуск (MCP-сервер должен быть уже запущен):

    python scripts/smoke_llm.py

Скрипт проверяет, что бот видит инструменты и отвечает по данным каталога.
Он не создаёт заявок и не меняет базу.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Запуск как `python scripts/smoke_llm.py` добавляет в sys.path папку scripts,
# поэтому корень проекта добавляем явно.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from telegram_bot.config import load_settings
from telegram_bot.context import AppContext

QUESTIONS = [
    "Здравствуйте! Хочу подтянуть разговорный английский. Сколько стоит и есть ли рассрочка?",
    "Какие курсы есть для начинающих до 18 000 рублей?",
    "Можно ли пропустить занятие и потом его отработать?",
]


async def main() -> None:
    """Прогнать несколько реплик через настоящий агент."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    app = AppContext.create(load_settings())
    tools = await app.mcp.ping()
    print("MCP-инструменты:", ", ".join(tool.name for tool in tools))
    print("=" * 70)

    for number, question in enumerate(QUESTIONS, start=1):
        print(f"Пользователь {number}: {question}")
        try:
            answer = await app.agent.run("smoke-test", question)
        except Exception as exc:  # noqa: BLE001 - smoke-тест должен показать любую ошибку
            print(f"ОШИБКА: {type(exc).__name__}: {exc}")
        else:
            print(f"Бот: {answer}")
        print("-" * 70)


if __name__ == "__main__":
    asyncio.run(main())
