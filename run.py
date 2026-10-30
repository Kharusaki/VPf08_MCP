"""CLI-интерфейс AI-агента.

Запуск:
    python run.py                 # интерактивный режим
    python run.py "вопрос"        # один вопрос и выход
"""

from __future__ import annotations

import argparse
import logging
import sys

from agent.agent import Agent, AgentError
from agent.config import SETTINGS
from agent.memory import clear_history, get_stats
from agent.tools.reminders import clear_reminders, list_reminders

logger = logging.getLogger(__name__)

BANNER = """AI-агент готов. Напишите задачу обычным языком.
Примеры:
  Какая погода в Москве?
  Сколько стоит Bitcoin в рублях?
  Напомни через 30 минут позвонить маме
  Сделай QR-код на https://example.com
  Посчитай (2+3)*sqrt(16)

Команды: /help, /clear, /stats, /reminders, /exit"""


def _setup_console() -> None:
    """Включить UTF-8 в консоли Windows, чтобы русский текст не ломался."""
    for stream in (sys.stdout, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _setup_logging() -> None:
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
    # Не засоряем консоль техническими логами HTTP-клиентов
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "primp", "ddgs"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _show_reply(reply) -> None:
    """Напечатать ответ и пути созданных файлов."""
    print(f"\nАгент: {reply.text}")
    for artifact in reply.artifacts:
        print(f"Файл: {artifact}")
    print()


def _help() -> None:
    print(BANNER)


def _stats() -> None:
    stats = get_stats()
    print(
        f"История: {stats['message_count']} сообщений\n"
        f"Ходов пользователя: {stats['total_turns']}\n"
        f"Обновлено: {stats['last_updated']}\n"
        f"Файл памяти: {stats['memory_file']}"
    )


def _reminders() -> None:
    items = list_reminders("cli")
    if not items:
        print("Активных напоминаний нет.")
        return
    print("Активные напоминания:")
    for item in items:
        print(f"- {item['when']} — {item['text']} (id: {item['id']})")


def main() -> None:
    """Точка входа CLI."""
    _setup_console()
    _setup_logging()

    parser = argparse.ArgumentParser(description="Локальный AI-агент")
    parser.add_argument("question", nargs="*", help="Вопрос для одного запуска")
    args = parser.parse_args()
    question = " ".join(args.question).strip()

    try:
        agent = Agent()
    except AgentError as exc:
        raise SystemExit(f"Ошибка запуска: {exc}") from exc

    if question:
        try:
            _show_reply(agent.run(question))
        except (AgentError, ValueError) as exc:
            raise SystemExit(f"Ошибка: {exc}") from exc
        return

    print(BANNER)
    while True:
        try:
            user = input("Вы: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nДо встречи!")
            break

        if not user:
            continue
        command = user.lower()
        if command in {"/exit", "/quit", "exit", "quit"}:
            print("До встречи!")
            break
        if command == "/help":
            _help()
            continue
        if command == "/clear":
            clear_history()
            print("История CLI очищена.")
            continue
        if command == "/stats":
            _stats()
            continue
        if command == "/reminders":
            _reminders()
            continue
        if command == "/clear_reminders":
            deleted = clear_reminders("cli")
            print(f"Удалено напоминаний: {deleted}")
            continue

        try:
            reply = agent.run(user)
            _show_reply(reply)
        except (AgentError, ValueError) as exc:
            print(f"\nОшибка: {exc}\n")
        except Exception as exc:  # чтобы CLI не закрывался из-за одного сбоя
            logger.exception("Неожиданная ошибка")
            print(f"\nНеожиданная ошибка: {exc}\n")


if __name__ == "__main__":
    main()
