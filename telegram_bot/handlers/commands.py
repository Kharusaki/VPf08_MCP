"""Команды Telegram-бота."""

from __future__ import annotations

import json
import logging

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from ..context import AppContext
from ..formatting import money, split_text
from ..mcp_client import MCPServiceError
from ..prompts import (
    HELP_TEXT,
    SERVICE_UNAVAILABLE_TEXT,
    WELCOME_TEXT,
)

logger = logging.getLogger(__name__)
router = Router(name="commands")


@router.message(CommandStart())
async def start_command(message: Message) -> None:
    """Поздороваться и объяснить, с чего начать."""
    await message.answer(WELCOME_TEXT)


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    """Показать возможности и список команд."""
    await message.answer(HELP_TEXT)


@router.message(Command("courses"))
async def courses_command(message: Message, app: AppContext) -> None:
    """Показать каталог напрямую из MCP, не тратя токены на LLM."""
    try:
        async with app.mcp.connect() as session:
            raw = await app.mcp.call_tool(session, "search_courses", {})
        data = json.loads(raw)
    except MCPServiceError:
        logger.exception("MCP недоступен при запросе каталога")
        await message.answer(SERVICE_UNAVAILABLE_TEXT)
        return
    except (json.JSONDecodeError, TypeError):
        logger.exception("MCP вернул некорректный каталог")
        await message.answer(SERVICE_UNAVAILABLE_TEXT)
        return

    courses = data.get("courses") if data.get("ok") else None
    if not courses:
        await message.answer(
            "Каталог сейчас недоступен. Напишите, какой язык вас интересует, "
            "и я помогу с подбором."
        )
        return

    lines = ["Каталог курсов «ЛингваПро»:", ""]
    for course in courses:
        lines.append(
            f"— {course['title']} ({course['level']}), "
            f"{course['weeks']} нед. — {money(course['price'])}"
        )
    lines.extend(
        [
            "",
            "Напишите, какой курс или язык интересует, — расскажу подробнее "
            "и помогу записаться на бесплатное пробное занятие.",
        ]
    )
    for chunk in split_text("\n".join(lines)):
        await message.answer(chunk)


@router.message(Command("clear"))
async def clear_command(message: Message, app: AppContext) -> None:
    """Очистить историю диалога текущего чата."""
    app.history.clear(message.chat.id)
    await message.answer("История этого диалога очищена. Можем начать заново.")


@router.message(Command("status"))
async def status_command(message: Message, app: AppContext) -> None:
    """Показать состояние MCP-подключения и истории."""
    try:
        tools = await app.mcp.ping()
        mcp_status = f"подключён, инструментов: {len(tools)}"
    except MCPServiceError:
        mcp_status = "недоступен"

    text = (
        "Статус бота:\n"
        f"— MCP-сервер: {mcp_status}\n"
        f"— История диалога: {app.history.count(message.chat.id)} сообщений\n"
        f"— Модель: {app.settings.default_model}"
    )
    await message.answer(text)
