"""Тесты напоминаний о заявке: расписание в базе и отправка ботом."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import pytest

from mcp_server.database import CourseDatabase
from telegram_bot.reminders import ReminderService


@pytest.fixture()
def db(tmp_path) -> CourseDatabase:
    """Отдельная временная база для каждого теста."""
    database = CourseDatabase(tmp_path / "courses.db")
    database.initialize()
    return database


def test_reminder_is_scheduled_and_due(db: CourseDatabase) -> None:
    reminder = db.schedule_reminder(chat_id=1, delay_seconds=0)
    assert reminder["status"] == "pending"
    assert [item["chat_id"] for item in db.due_reminders()] == [1]


def test_reminder_is_not_due_before_delay(db: CourseDatabase) -> None:
    db.schedule_reminder(chat_id=1, delay_seconds=120)
    assert db.due_reminders() == []


def test_reminder_rescheduled_from_last_message(db: CourseDatabase) -> None:
    first = db.schedule_reminder(chat_id=1, delay_seconds=0)
    second = db.schedule_reminder(chat_id=1, delay_seconds=120)
    assert second["due_at"] > first["due_at"]
    assert db.due_reminders() == []


def test_reminder_sent_only_once(db: CourseDatabase) -> None:
    db.schedule_reminder(chat_id=1, delay_seconds=0)
    assert db.mark_reminder_sent(1) is True
    assert db.mark_reminder_sent(1) is False
    assert db.due_reminders() == []

    # После отправки новый таймер не создаётся: одно напоминание на диалог.
    db.schedule_reminder(chat_id=1, delay_seconds=0)
    assert db.due_reminders() == []


def test_lead_created_cancels_reminder(db: CourseDatabase) -> None:
    db.schedule_reminder(chat_id=1, delay_seconds=0)
    db.mark_lead_created(1)
    assert db.due_reminders() == []
    assert db.reminder_status(1)["lead_created"] == 1

    db.schedule_reminder(chat_id=1, delay_seconds=0)
    assert db.due_reminders() == []


def test_reset_allows_reminder_again(db: CourseDatabase) -> None:
    db.schedule_reminder(chat_id=1, delay_seconds=0)
    db.mark_reminder_sent(1)
    db.reset_reminder(1)
    assert db.reminder_status(1) is None

    db.schedule_reminder(chat_id=1, delay_seconds=0)
    assert [item["chat_id"] for item in db.due_reminders()] == [1]


class FakeMCP:
    """Поддельный MCP-клиент с расписанием в памяти."""

    def __init__(self, due: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.due = due or []

    @asynccontextmanager
    async def connect(self):
        yield object()

    async def call_tool(self, _session: Any, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((name, arguments))
        if name == "get_due_reminders":
            return json.dumps(
                {"ok": True, "count": len(self.due), "text": "Напоминание", "reminders": self.due},
                ensure_ascii=False,
            )
        if name == "get_lead_reminder_status":
            return json.dumps({"ok": True, "status": "pending", "due_at": "2026-01-01 00:00:00"})
        return json.dumps({"ok": True}, ensure_ascii=False)


class FakeBot:
    """Запоминает отправленные сообщения."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


@pytest.mark.asyncio
async def test_service_schedules_reminder_after_message() -> None:
    mcp = FakeMCP()
    service = ReminderService(mcp_client=mcp, delay_seconds=120)  # type: ignore[arg-type]

    await service.touch(42)

    assert mcp.calls == [("schedule_lead_reminder", {"chat_id": 42, "delay_seconds": 120})]


@pytest.mark.asyncio
async def test_service_disabled_without_delay() -> None:
    mcp = FakeMCP()
    service = ReminderService(mcp_client=mcp, delay_seconds=0)  # type: ignore[arg-type]

    await service.touch(42)

    assert service.enabled is False
    assert mcp.calls == []
    assert await service.status(42) == "выключено"


@pytest.mark.asyncio
async def test_service_sends_due_reminder_once() -> None:
    mcp = FakeMCP(due=[{"chat_id": 7, "due_at": "2026-01-01 00:00:00"}])
    bot = FakeBot()
    service = ReminderService(mcp_client=mcp, delay_seconds=120, sender=bot)  # type: ignore[arg-type]

    await service._dispatch_due()

    assert bot.sent == [(7, "Напоминание")]
    assert ("complete_lead_reminder", {"chat_id": 7}) in mcp.calls


@pytest.mark.asyncio
async def test_service_skips_send_without_bot() -> None:
    mcp = FakeMCP(due=[{"chat_id": 7, "due_at": "2026-01-01 00:00:00"}])
    service = ReminderService(mcp_client=mcp, delay_seconds=120)  # type: ignore[arg-type]

    await service._dispatch_due()

    assert ("complete_lead_reminder", {"chat_id": 7}) not in mcp.calls
