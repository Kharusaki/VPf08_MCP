"""Напоминание о заявке, если диалог закончился без лида.

Расписание хранится на MCP-сервере (таблица ``reminders``), а бот только
перезапускает таймер после ответа и периодически опрашивает сервер. Поэтому
перезапуск бота не теряет запланированные напоминания, а логика «когда
напоминать» остаётся на сервере, как и остальные данные проекта.

Правила:

* после каждого ответа бота таймер чата перезапускается;
* если за это время заявка создана, напоминание отменяется;
* одно напоминание отправляется не больше одного раза за диалог;
* ``/clear`` сбрасывает состояние и позволяет показать напоминание снова.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Protocol

from .mcp_client import MCPClient, MCPServiceError

logger = logging.getLogger(__name__)


class MessageSender(Protocol):
    """Минимальный интерфейс отправителя: в боте это ``aiogram.Bot``."""

    async def send_message(self, chat_id: int, text: str) -> Any: ...


class ReminderService:
    """Связывает бота с расписанием напоминаний на MCP-сервере."""

    def __init__(
        self,
        mcp_client: MCPClient,
        delay_seconds: float,
        poll_seconds: float = 15.0,
        sender: MessageSender | None = None,
    ) -> None:
        self.mcp_client = mcp_client
        self.delay_seconds = max(0.0, float(delay_seconds))
        self.poll_seconds = max(1.0, float(poll_seconds))
        self._sender = sender
        self._task: asyncio.Task[None] | None = None

    @property
    def enabled(self) -> bool:
        """Напоминания выключены, если задержка равна нулю."""
        return self.delay_seconds > 0

    def bind_sender(self, sender: MessageSender) -> None:
        """Подключить отправителя после создания ``Bot``."""
        self._sender = sender

    async def start(self) -> None:
        """Запустить фоновый опрос сервера."""
        if not self.enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._poll_loop(), name="lead-reminder-poll")
        logger.info(
            "Напоминания включены: через %g с, опрос каждые %g с",
            self.delay_seconds,
            self.poll_seconds,
        )

    async def close(self) -> None:
        """Остановить опрос при завершении бота."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def touch(self, chat_id: int | str) -> None:
        """Перезапустить таймер после сообщения пользователя."""
        if not self.enabled:
            return
        await self._call(
            "schedule_lead_reminder",
            {"chat_id": int(chat_id), "delay_seconds": int(self.delay_seconds)},
        )

    async def mark_lead_created(self, chat_id: int | str) -> None:
        """Заявка создана: отменить таймер и больше не напоминать."""
        await self._call(
            "cancel_lead_reminder",
            {"chat_id": int(chat_id), "reason": "lead_created"},
        )

    async def reset(self, chat_id: int | str) -> None:
        """Забыть состояние чата, например после ``/clear``."""
        await self._call("cancel_lead_reminder", {"chat_id": int(chat_id)})

    async def status(self, chat_id: int | str) -> str:
        """Короткое описание состояния напоминания для ``/status``."""
        if not self.enabled:
            return "выключено"
        data = await self._call("get_lead_reminder_status", {"chat_id": int(chat_id)})
        if data is None:
            return "не запланировано"
        status = data.get("status")
        if data.get("lead_created") or status == "cancelled":
            return "не требуется, заявка создана"
        if status == "sent":
            return "уже отправлено"
        if status == "pending":
            return f"запланировано на {data.get('due_at', '')} UTC"
        return "не запланировано"

    async def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """Вызвать инструмент напоминаний, не ломая ответ пользователю."""
        try:
            async with self.mcp_client.connect() as session:
                raw = await self.mcp_client.call_tool(session, tool, arguments)
            data = json.loads(raw)
        except (MCPServiceError, json.JSONDecodeError, TypeError):
            logger.warning("Не удалось вызвать %s: MCP недоступен", tool)
            return None
        if not isinstance(data, dict) or not data.get("ok"):
            logger.warning("Инструмент %s вернул ошибку: %s", tool, raw)
            return None
        return data

    async def _poll_loop(self) -> None:
        """Периодически забирать созревшие напоминания и отправлять их."""
        while True:
            try:
                await self._dispatch_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Ошибка при отправке напоминаний")
            await asyncio.sleep(self.poll_seconds)

    async def _dispatch_due(self) -> None:
        """Отправить все напоминания, время которых наступило."""
        data = await self._call("get_due_reminders", {"limit": 20})
        if not data:
            return

        text = str(data.get("text") or "").strip()
        reminders = data.get("reminders") or []
        if not text or not reminders:
            return

        sender = self._sender
        if sender is None:
            logger.warning("Напоминания не отправлены: Bot ещё не подключён")
            return

        for item in reminders:
            chat_id = int(item["chat_id"])
            try:
                await sender.send_message(chat_id, text)
            except Exception:
                logger.exception("Не удалось отправить напоминание в чат %s", chat_id)
                continue
            await self._call("complete_lead_reminder", {"chat_id": chat_id})
            logger.info("Напоминание о заявке отправлено в чат %s", chat_id)