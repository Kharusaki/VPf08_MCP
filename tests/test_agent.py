"""Тест агентного цикла с поддельными LLM и MCP.

Тест не обращается ни к сети, ни к Telegram: проверяется именно логика
«модель попросила инструмент → инструмент вызван → ответ сохранён».
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from telegram_bot.agent import SalesAgent
from telegram_bot.config import Settings
from telegram_bot.history import HistoryStore


class FakeMessage:
    """Минимальная копия message из OpenAI SDK."""

    def __init__(self, content: str | None = None, tool_calls: list[Any] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, exclude_none: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "role": "assistant",
            "content": self.content,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in self.tool_calls or []
            ],
        }
        if not self.tool_calls:
            data.pop("tool_calls")
        if exclude_none:
            data = {key: value for key, value in data.items() if value is not None}
        return data


def fake_response(content: str | None = None, tool_calls: list[Any] | None = None):
    """Собрать ответ в форме, которую ожидает агент."""
    message = FakeMessage(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeCompletions:
    """Поддельный chat.completions с заранее заданной очередью ответов."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any):
        self.requests.append(kwargs)
        return self.responses.pop(0)


class FakeOpenAI:
    """Минимальная замена AsyncOpenAI."""

    def __init__(self, responses: list[Any]) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions(responses))


class FakeMCP:
    """Поддельный MCP-клиент с одним инструментом."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    @asynccontextmanager
    async def connect(self):
        yield object()

    async def list_tools(self, _session: Any):
        return [
            SimpleNamespace(
                name="search_courses",
                description="Найти курсы",
                inputSchema={
                    "type": "object",
                    "properties": {"language": {"type": "string"}},
                },
            )
        ]

    async def call_tool(self, _session: Any, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((name, arguments))
        return '{"ok":true,"count":1,"courses":[{"id":1,"title":"Английский с нуля"}]}'


def make_settings(tmp_path: Path) -> Settings:
    """Настройки без чтения реального .env."""
    return Settings(
        bot_token="test",
        telegram_proxy="",
        openai_api_key="test",
        openai_base_url="https://example.invalid/v1",
        default_model="test-model",
        fallback_model="",
        temperature=0.3,
        max_tokens=500,
        request_timeout=10,
        mcp_url="http://127.0.0.1:1/mcp",
        mcp_timeout=1,
        mcp_read_timeout=1,
        max_tool_rounds=3,
        max_tool_result_chars=1000,
        history_file=tmp_path / "history.json",
        history_limit=10,
        history_max_chars=5000,
        max_message_chars=1000,
        log_file=tmp_path / "bot.log",
    )


@pytest.mark.asyncio
async def test_agent_calls_tool_and_saves_history(tmp_path) -> None:
    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="search_courses", arguments='{"language":"английский"}'),
    )
    fake_openai = FakeOpenAI(
        [
            fake_response(tool_calls=[tool_call]),
            fake_response(content="Подойдёт курс «Английский с нуля»."),
        ]
    )
    fake_mcp = FakeMCP()
    history = HistoryStore(tmp_path / "history.json", limit=10)
    agent = SalesAgent(
        settings=make_settings(tmp_path),
        mcp_client=fake_mcp,  # type: ignore[arg-type]
        history=history,
        openai_client=fake_openai,  # type: ignore[arg-type]
    )

    answer = await agent.run(1, "Какой курс выбрать?")

    assert answer == "Подойдёт курс «Английский с нуля»."
    assert fake_mcp.calls == [("search_courses", {"language": "английский"})]
    assert history.get(1) == [
        {"role": "user", "content": "Какой курс выбрать?"},
        {"role": "assistant", "content": "Подойдёт курс «Английский с нуля»."},
    ]
    assert fake_openai.chat.completions.requests[1]["messages"][-1]["role"] == "tool"


@pytest.mark.asyncio
async def test_agent_returns_tool_error_to_model(tmp_path) -> None:
    tool_call = SimpleNamespace(
        id="call_bad",
        function=SimpleNamespace(name="search_courses", arguments="{битый json"),
    )
    fake_openai = FakeOpenAI(
        [
            fake_response(tool_calls=[tool_call]),
            fake_response(content="Уточните, пожалуйста, запрос."),
        ]
    )
    agent = SalesAgent(
        settings=make_settings(tmp_path),
        mcp_client=FakeMCP(),  # type: ignore[arg-type]
        history=HistoryStore(tmp_path / "history.json"),
        openai_client=fake_openai,  # type: ignore[arg-type]
    )

    answer = await agent.run(1, "Найди курс")

    assert answer == "Уточните, пожалуйста, запрос."
    tool_message = fake_openai.chat.completions.requests[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert "Некорректный JSON" in tool_message["content"]
