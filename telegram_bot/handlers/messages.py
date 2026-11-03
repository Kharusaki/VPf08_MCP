"""Обработчики обычных сообщений."""

from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.types import Message

from ..agent import AgentError
from ..context import AppContext
from ..formatting import split_text
from ..mcp_client import MCPServiceError
from ..prompts import (
    INTERNAL_ERROR_TEXT,
    LLM_UNAVAILABLE_TEXT,
    SERVICE_UNAVAILABLE_TEXT,
    UNKNOWN_COMMAND_TEXT,
    UNSUPPORTED_TEXT,
)

logger = logging.getLogger(__name__)
router = Router(name="messages")

# Один пользователь — один активный запрос. Иначе два быстрых сообщения
# могли бы одновременно менять историю и получить перепутанный контекст.
_locks: dict[int, asyncio.Lock] = {}


def _chat_lock(chat_id: int) -> asyncio.Lock:
    """Вернуть блокировку конкретного чата."""
    return _locks.setdefault(chat_id, asyncio.Lock())


@router.message(F.text & ~F.text.startswith("/"))
async def text_message(message: Message, app: AppContext) -> None:
    """Передать текст агенту и отправить ответ частями."""
    chat_id = message.chat.id
    try:
        await message.bot.send_chat_action(chat_id=chat_id, action="typing")
        max_tokens = app.preferences.max_tokens_for(chat_id, app.settings.max_tokens)
        async with _chat_lock(chat_id):
            answer = await app.agent.run(
                chat_id,
                message.text or "",
                max_tokens=max_tokens,
            )
    except MCPServiceError:
        logger.exception("MCP-сервер недоступен при обработке сообщения")
        await message.answer(SERVICE_UNAVAILABLE_TEXT)
        return
    except AgentError:
        logger.exception("Ошибка агента при обработке сообщения")
        await message.answer(LLM_UNAVAILABLE_TEXT)
        return
    except Exception:
        logger.exception("Неожиданная ошибка при обработке сообщения")
        await message.answer(INTERNAL_ERROR_TEXT)
        return

    for chunk in split_text(answer):
        await message.answer(chunk)

    # Таймер напоминания отсчитывается от последнего ответа бота: если клиент
    # уйдёт без заявки, сервер пришлёт одно напоминание.
    await app.reminders.touch(chat_id)


@router.message(F.text.startswith("/"))
async def unknown_command(message: Message) -> None:
    """Подсказать, что такой команды нет."""
    await message.answer(UNKNOWN_COMMAND_TEXT)


@router.message()
async def unsupported_message(message: Message) -> None:
    """Мягко объяснить, что бот пока понимает только текст."""
    await message.answer(UNSUPPORTED_TEXT)
