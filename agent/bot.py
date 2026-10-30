"""Telegram-бот для AI-агента.

Запуск: python run_bot.py
Бот использует те же инструменты и память, что и CLI, но хранит историю
отдельно для каждого чата и умеет присылать напоминания.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from telegram import InputFile, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .agent import Agent, AgentError
from .config import SETTINGS
from .memory import clear_history, get_stats
from .tools.reminders import get_due_reminders, list_reminders, mark_reminder_sent

logger = logging.getLogger(__name__)

_AGENT: Optional[Agent] = None

HELP_TEXT = """Я локальный AI-агент. Умею:

- искать информацию в интернете (DuckDuckGo);
- узнавать погоду в любом городе;
- показывать курс криптовалют (Bitcoin, Ethereum, TON и др.);
- пересчитывать обычные валюты (USD, EUR, RUB и др.);
- читать и записывать файлы в папке workspace;
- выполнять безопасные read-only команды;
- делать QR-коды;
- считать сложные математические выражения;
- ставить напоминания.

Просто напишите задачу обычным языком. Например:
«Какая погода в Москве?»
«Сколько сейчас стоит Bitcoin в рублях?»
«Напомни через 30 минут позвонить маме».
«Сделай QR-код на https://example.com».

Команды:
/start — приветствие
/help — эта справка
/clear — очистить историю этого чата
/stats — статистика памяти
/reminders — мои напоминания
/id — ID этого чата"""


def _get_agent() -> Agent:
    """Создать агента при первом обращении (чтобы импорт не падал без ключа)."""
    global _AGENT
    if _AGENT is None:
        _AGENT = Agent()
    return _AGENT


def _configure_logging() -> None:
    """Логи в файл и в консоль."""
    SETTINGS.log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(SETTINGS.log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    # В URL запросов Telegram есть токен — не пишем такие строки в лог
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "primp", "ddgs", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /start."""
    if update.message:
        await update.message.reply_text(
            "Привет! Я AI-агент с инструментами.\n\n" + HELP_TEXT
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /help."""
    if update.message:
        await update.message.reply_text(HELP_TEXT)


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /clear — очистить историю текущего чата."""
    chat_id = str(update.effective_chat.id)
    await asyncio.to_thread(clear_history, chat_id)
    if update.message:
        await update.message.reply_text("История этого чата очищена.")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /stats."""
    chat_id = str(update.effective_chat.id)
    stats = await asyncio.to_thread(get_stats, chat_id)
    text = (
        f"История: {stats['message_count']} сообщений\n"
        f"Ходов пользователя: {stats['total_turns']}\n"
        f"Обновлено: {stats['last_updated']}\n"
        f"Файл памяти: {stats['memory_file']}"
    )
    if update.message:
        await update.message.reply_text(text)


async def reminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /reminders."""
    chat_id = str(update.effective_chat.id)
    items = await asyncio.to_thread(list_reminders, chat_id, False)
    if not items:
        if update.message:
            await update.message.reply_text("Активных напоминаний нет.")
        return

    lines = ["Активные напоминания:"]
    for item in items:
        when = str(item.get("when", ""))
        try:
            when = datetime.fromisoformat(when).strftime("%d.%m.%Y %H:%M")
        except ValueError:
            pass
        lines.append(f"- {when} — {item.get('text')} (id: {item.get('id')})")
    if update.message:
        await update.message.reply_text("\n".join(lines))


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /id — показать ID чата."""
    if update.message:
        await update.message.reply_text(f"ID этого чата: {update.effective_chat.id}")


def _split_text(text: str, limit: int = 4000) -> list[str]:
    """Разбить длинный ответ на части по лимиту Telegram."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return chunks


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработать обычный текст пользователя."""
    if not update.message or not update.message.text:
        return

    chat_id = str(update.effective_chat.id)
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        reply = await asyncio.to_thread(_get_agent().run, update.message.text, chat_id)
    except AgentError as exc:
        logger.exception("Ошибка агента")
        await update.message.reply_text(f"Не удалось получить ответ: {exc}")
        return
    except Exception as exc:
        logger.exception("Неожиданная ошибка")
        await update.message.reply_text(f"Произошла ошибка: {exc}")
        return

    for chunk in _split_text(reply.text):
        await update.message.reply_text(chunk)

    # QR-коды и другие созданные файлы отправляем вложениями
    for artifact in reply.artifacts:
        path = Path(artifact)
        if not path.is_file():
            continue
        try:
            with open(path, "rb") as file_obj:
                await update.message.reply_photo(
                    photo=InputFile(file_obj, filename=path.name),
                    caption=f"Файл: {path.name}",
                )
        except Exception as exc:
            logger.warning("Не удалось отправить файл %s: %s", path, exc)


async def check_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Фоновая задача: отправить наступившие напоминания."""
    try:
        due = await asyncio.to_thread(get_due_reminders)
    except Exception:
        logger.exception("Не удалось прочитать напоминания")
        return

    for item in due:
        reminder_id = item.get("id")
        chat_id = item.get("chat_id")
        if not reminder_id or not chat_id or str(chat_id) == "cli":
            if reminder_id:
                await asyncio.to_thread(mark_reminder_sent, reminder_id)
            continue

        try:
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=f"Напоминание: {item.get('text')}",
            )
            await asyncio.to_thread(mark_reminder_sent, reminder_id)
        except Exception as exc:
            logger.warning("Не удалось отправить напоминание %s: %s", reminder_id, exc)
            # Если чат недоступен, повторять бессмысленно
            if "Forbidden" in str(exc) or "chat not found" in str(exc).lower():
                await asyncio.to_thread(mark_reminder_sent, reminder_id)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Логировать ошибки Telegram и сообщить пользователю."""
    logger.exception("Ошибка Telegram", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Произошла внутренняя ошибка. Попробуйте ещё раз."
            )
        except Exception:
            pass


def main() -> None:
    """Точка входа Telegram-бота."""
    _configure_logging()

    if not SETTINGS.bot_token:
        raise SystemExit("В .env не задан BOT_TOKEN.")
    if not SETTINGS.api_key:
        raise SystemExit("В .env не задан OPENAI_API_KEY.")

    builder = Application.builder().token(SETTINGS.bot_token)
    if SETTINGS.telegram_proxy:
        builder = builder.proxy(SETTINGS.telegram_proxy)
    application = builder.build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("clear", clear_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("reminders", reminders_command))
    application.add_handler(CommandHandler("id", id_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    if application.job_queue:
        application.job_queue.run_repeating(
            check_reminders,
            interval=SETTINGS.reminder_check_interval,
            first=5,
        )

    logger.info("Telegram-бот запущен. Нажмите Ctrl+C для остановки.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
