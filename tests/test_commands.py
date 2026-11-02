"""Тесты команды /length без запуска Telegram."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from telegram_bot.handlers.commands import length_command
from telegram_bot.preferences import PreferencesStore


class FakeMessage:
    """Минимальная замена aiogram Message для проверки ответа."""

    def __init__(self, chat_id: int = 42) -> None:
        self.chat = SimpleNamespace(id=chat_id)
        self.answers: list[str] = []

    async def answer(self, text: str) -> None:
        self.answers.append(text)


class FakeApp:
    """Только те зависимости, которые нужны команде /length."""

    def __init__(self, tmp_path) -> None:
        self.preferences = PreferencesStore(tmp_path / "preferences.json")
        self.settings = SimpleNamespace(max_tokens=650)


@pytest.mark.asyncio
async def test_length_command_shows_current_mode(tmp_path) -> None:
    message = FakeMessage()
    app = FakeApp(tmp_path)

    await length_command(message, SimpleNamespace(args=None), app)  # type: ignore[arg-type]

    assert "обычный" in message.answers[-1]
    assert "/length long" in message.answers[-1]


@pytest.mark.asyncio
async def test_length_command_sets_mode(tmp_path) -> None:
    message = FakeMessage()
    app = FakeApp(tmp_path)

    await length_command(message, SimpleNamespace(args="длинный"), app)  # type: ignore[arg-type]

    assert app.preferences.get_answer_length(42) == "long"
    assert "длинный" in message.answers[-1]
    assert "1600" in message.answers[-1]
