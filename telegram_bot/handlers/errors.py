"""Глобальный обработчик ошибок aiogram."""

from __future__ import annotations

import logging

from aiogram.types import ErrorEvent

from ..prompts import INTERNAL_ERROR_TEXT

logger = logging.getLogger(__name__)


async def on_error(event: ErrorEvent) -> None:
    """Записать ошибку в лог и не оставить пользователя без ответа."""
    error = event.exception
    logger.error(
        "Необработанная ошибка Telegram-бота",
        exc_info=(type(error), error, error.__traceback__),
    )
    message = event.update.effective_message if event.update else None
    if message is None:
        return
    try:
        await message.answer(INTERNAL_ERROR_TEXT)
    except Exception:
        logger.debug("Не удалось отправить сообщение об ошибке", exc_info=True)
