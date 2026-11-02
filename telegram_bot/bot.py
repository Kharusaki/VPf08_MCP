"""Сборка и запуск Telegram-бота на aiogram 3."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import (
    TelegramConflictError,
    TelegramNetworkError,
    TelegramUnauthorizedError,
)
from aiogram.types import BotCommand

from .config import Settings, load_settings
from .context import AppContext
from .handlers import router as main_router
from .handlers.errors import on_error
from .mcp_client import MCPServiceError

logger = logging.getLogger(__name__)


def configure_logging(settings: Settings) -> None:
    """Настроить консоль и файл логов, приглушив шум библиотек."""
    settings.log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(settings.log_file, encoding="utf-8"),
        ],
    )
    for noisy in ("httpx", "httpcore", "aiogram.event", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def create_dispatcher(app: AppContext) -> Dispatcher:
    """Собрать dispatcher и передать в хендлеры общие зависимости."""
    dispatcher = Dispatcher()
    dispatcher["app"] = app
    dispatcher.include_router(main_router)
    dispatcher.errors.register(on_error)
    return dispatcher


async def _setup_bot(bot: Bot) -> None:
    """Убрать старый webhook и опубликовать меню команд (best effort)."""
    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception as exc:
        logger.warning("Не удалось удалить webhook: %s", exc)

    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Начать диалог"),
                BotCommand(command="courses", description="Каталог курсов"),
                BotCommand(command="help", description="Помощь"),
                BotCommand(command="clear", description="Очистить историю"),
                BotCommand(command="status", description="Статус бота"),
            ]
        )
    except Exception as exc:
        logger.warning("Не удалось обновить меню команд: %s", exc)


def _build_bot(settings: Settings) -> Bot:
    """Создать Bot, при необходимости с прокси для Telegram."""
    session: AiohttpSession | None = None
    if settings.telegram_proxy:
        session = AiohttpSession(proxy=settings.telegram_proxy)
    if session is not None:
        return Bot(token=settings.bot_token, session=session)
    return Bot(token=settings.bot_token)


async def run(settings: Settings | None = None) -> None:
    """Запустить polling до остановки процесса."""
    settings = settings or load_settings()
    configure_logging(settings)

    missing = settings.missing_required()
    if missing:
        raise SystemExit(
            "В .env не заданы обязательные переменные: "
            + ", ".join(missing)
            + ". Заполните .env по образцу .env.example."
        )

    app = AppContext.create(settings)
    bot = _build_bot(settings)
    dispatcher = create_dispatcher(app)

    try:
        # Недоступный MCP не мешает боту запуститься: команды /start и /help
        # продолжат работать, а на запросы бот ответит понятным сообщением.
        try:
            tools = await app.mcp.ping()
            logger.info("MCP-сервер доступен, инструментов: %d", len(tools))
        except MCPServiceError:
            logger.warning(
                "MCP-сервер недоступен по адресу %s. "
                "Запустите его командой python run_server.py.",
                settings.mcp_url,
            )

        await _setup_bot(bot)
        logger.info("Бот запущен. Нажмите Ctrl+C для остановки.")
        await dispatcher.start_polling(bot)
    except TelegramUnauthorizedError:
        logger.error("Telegram отклонил BOT_TOKEN. Проверьте токен в .env.")
        raise SystemExit(1)
    except TelegramConflictError:
        logger.error(
            "Этот BOT_TOKEN уже используется другим запущенным экземпляром бота. "
            "Остановите вторую копию."
        )
        raise SystemExit(1)
    except TelegramNetworkError as exc:
        logger.error("Нет соединения с Telegram: %s", exc)
        raise SystemExit(1)
    finally:
        await bot.session.close()


def main() -> None:
    """Синхронная точка входа для run_bot.py и python -m telegram_bot."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем.")


if __name__ == "__main__":
    main()
