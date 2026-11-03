"""Общие зависимости Telegram-бота.

Создаются один раз при старте и передаются в хендлеры через dispatcher.
"""

from __future__ import annotations

from dataclasses import dataclass

from .agent import SalesAgent
from .config import Settings, load_settings
from .history import HistoryStore
from .mcp_client import MCPClient
from .preferences import PreferencesStore
from .reminders import ReminderService


@dataclass(frozen=True)
class AppContext:
    """Набор сервисов, нужных командам и обработчикам сообщений."""

    settings: Settings
    history: HistoryStore
    preferences: PreferencesStore
    mcp: MCPClient
    agent: SalesAgent
    reminders: ReminderService

    @classmethod
    def create(cls, settings: Settings | None = None) -> AppContext:
        """Собрать зависимости из настроек."""
        settings = settings or load_settings()
        history = HistoryStore(
            settings.history_file,
            limit=settings.history_limit,
            max_chars=settings.history_max_chars,
            max_message_chars=settings.max_message_chars,
        )
        preferences = PreferencesStore(settings.preferences_file)
        mcp = MCPClient(
            settings.mcp_url,
            timeout=settings.mcp_timeout,
            read_timeout=settings.mcp_read_timeout,
        )
        reminders = ReminderService(
            mcp_client=mcp,
            delay_seconds=settings.reminder_delay_seconds,
            poll_seconds=settings.reminder_poll_seconds,
        )
        agent = SalesAgent(
            settings=settings,
            mcp_client=mcp,
            history=history,
            on_lead_created=reminders.mark_lead_created,
        )
        return cls(
            settings=settings,
            history=history,
            preferences=preferences,
            mcp=mcp,
            agent=agent,
            reminders=reminders,
        )
