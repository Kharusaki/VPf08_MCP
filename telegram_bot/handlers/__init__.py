"""Маршрутизаторы Telegram-бота.

Порядок важен: сначала команды, затем обычный текст и только потом заглушки
для неизвестных команд и нетекстовых сообщений.
"""

from aiogram import Router

from . import commands, messages

router = Router(name="root")
router.include_router(commands.router)
router.include_router(messages.router)

__all__ = ["router"]
