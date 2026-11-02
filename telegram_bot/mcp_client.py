"""Асинхронный клиент MCP поверх Streamable HTTP.

Клиент открывает одно соединение на один ход диалога. Этого достаточно: агент
может вызвать несколько инструментов подряд в рамках одной сессии, а после
ответа соединение корректно закрывается.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from typing import Any
from urllib.parse import urlparse

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Tool

logger = logging.getLogger(__name__)


class MCPServiceError(RuntimeError):
    """MCP-сервер недоступен или транспортная ошибка при вызове."""


class MCPClient:
    """Минимальная обёртка над официальным MCP SDK."""

    def __init__(self, url: str, timeout: int = 30, read_timeout: int = 60) -> None:
        self.url = url
        self.timeout = max(1, int(timeout))
        self.read_timeout = max(1, int(read_timeout))

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[ClientSession]:
        """Открыть сессию MCP и закрыть её после выхода из контекста."""
        await self._ensure_reachable()
        stack = AsyncExitStack()
        try:
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(
                    timeout=httpx.Timeout(self.timeout, read=self.read_timeout),
                    follow_redirects=True,
                )
            )
            read_stream, write_stream, _ = await stack.enter_async_context(
                streamable_http_client(
                    self.url,
                    http_client=http_client,
                )
            )
            session = await stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()
        except Exception as exc:
            # MCP SDK может оставить незакрытый task group при обрыве соединения.
            # Подавляем шум очистки: пользователю важнее понятная ошибка сервиса.
            with suppress(Exception):
                await stack.aclose()
            logger.warning("MCP-сервер недоступен (%s): %s", self.url, exc)
            raise MCPServiceError("MCP-сервер недоступен") from exc

        try:
            yield session
        finally:
            with suppress(Exception):
                await stack.aclose()

    async def _ensure_reachable(self) -> None:
        """Быстро проверить TCP-порт до запуска MCP-хендшейка.

        Это отсекает типичный сценарий «сервер не запущен» до того, как SDK
        создаст task group и начнёт шумно закрывать его после ошибки.
        """
        parsed = urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise MCPServiceError("Некорректный URL MCP-сервера")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(parsed.hostname, port),
                timeout=min(3, self.timeout),
            )
        except (OSError, asyncio.TimeoutError) as exc:
            logger.warning("MCP-сервер недоступен (%s): %s", self.url, exc)
            raise MCPServiceError("MCP-сервер недоступен") from exc
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()

    async def list_tools(self, session: ClientSession) -> list[Tool]:
        """Получить список инструментов, зарегистрированных на сервере."""
        try:
            result = await session.list_tools()
        except Exception as exc:
            raise MCPServiceError("Не удалось получить список MCP-инструментов") from exc
        return list(result.tools)

    async def call_tool(
        self,
        session: ClientSession,
        name: str,
        arguments: dict[str, Any],
    ) -> str:
        """Вызвать инструмент и вернуть его текстовый результат."""
        try:
            result = await session.call_tool(name, arguments)
        except Exception as exc:
            logger.warning("Ошибка вызова MCP-инструмента %s: %s", name, exc)
            raise MCPServiceError(f"Ошибка вызова инструмента {name}") from exc
        return self._result_text(result)

    async def ping(self) -> list[Tool]:
        """Проверить соединение и вернуть список инструментов."""
        async with self.connect() as session:
            return await self.list_tools(session)

    @staticmethod
    def _result_text(result: Any) -> str:
        """Достать текст из CallToolResult, сохранив ошибку инструмента."""
        chunks: list[str] = []
        for item in getattr(result, "content", []) or []:
            text = getattr(item, "text", None)
            if text:
                chunks.append(str(text))

        if not chunks:
            structured = getattr(result, "structuredContent", None)
            if structured is not None:
                chunks.append(
                    json.dumps(structured, ensure_ascii=False, separators=(",", ":"))
                )

        text = "\n".join(chunks).strip()
        if getattr(result, "isError", False):
            return json.dumps(
                {"ok": False, "error": text or "Инструмент завершился с ошибкой"},
                ensure_ascii=False,
            )
        return text or '{"ok":false,"error":"Инструмент вернул пустой ответ"}'
