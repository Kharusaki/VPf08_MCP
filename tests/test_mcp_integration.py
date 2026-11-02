"""Интеграционный тест: настоящий MCP-сервер + настоящий MCP-клиент.

Проверяется транспорт Streamable HTTP, регистрация инструментов и бизнес-логика
каталога. Telegram и LLM здесь не участвуют.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from telegram_bot.mcp_client import MCPClient, MCPServiceError

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    """Получить свободный TCP-порт для тестового сервера."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def server(tmp_path):
    """Запустить MCP-сервер отдельным процессом на временной базе."""
    port = _free_port()
    env = os.environ.copy()
    env["MCP_HOST"] = "127.0.0.1"
    env["MCP_PORT"] = str(port)
    env["MCP_DB_PATH"] = str(tmp_path / "integration.db")

    process = subprocess.Popen(
        [sys.executable, "-m", "mcp_server"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    yield f"http://127.0.0.1:{port}/mcp", process
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


async def _wait_for_server(client: MCPClient, process: subprocess.Popen[bytes]):
    """Ждать готовности сервера, не зависая навсегда."""
    for _ in range(80):
        if process.poll() is not None:
            raise AssertionError("MCP-сервер завершился при запуске")
        try:
            return await client.ping()
        except MCPServiceError:
            await asyncio.sleep(0.25)
    raise AssertionError("MCP-сервер не стал доступен за 20 секунд")


@pytest.mark.asyncio
async def test_mcp_end_to_end(server) -> None:
    url, process = server
    client = MCPClient(url, timeout=10, read_timeout=20)
    tools = await _wait_for_server(client, process)
    names = {tool.name for tool in tools}

    assert {
        "search_courses",
        "get_course_details",
        "calculate_course_price",
        "list_promotions",
        "search_faq",
        "create_lead",
    } <= names

    async with client.connect() as session:
        raw = await client.call_tool(
            session, "search_courses", {"language": "english"}
        )
        courses = json.loads(raw)
        assert courses["ok"] is True
        assert courses["count"] == 4

        raw = await client.call_tool(
            session, "get_course_details", {"course_id": 1}
        )
        details = json.loads(raw)
        assert details["ok"] is True
        assert details["course"]["title"] == "Английский с нуля"
        assert len(details["course"]["modules"]) == 3

        raw = await client.call_tool(
            session,
            "calculate_course_price",
            {"course_id": 1, "promo_code": "START10", "installment_months": 6},
        )
        price = json.loads(raw)
        assert price["ok"] is True
        assert price["discount_rub"] == 1499
        assert price["monthly_payment"] > 0
        assert {item["months"] for item in price["installment_options"]} == {3, 6, 12}

        raw = await client.call_tool(session, "list_promotions", {})
        promos = json.loads(raw)
        assert {item["code"] for item in promos["promotions"]} == {
            "START10",
            "GROUP15",
            "IELTS5",
        }

        raw = await client.call_tool(
            session, "search_faq", {"query": "можно ли оплатить в рассрочку"}
        )
        faq = json.loads(raw)
        assert faq["ok"] is True
        assert faq["count"] >= 1

        raw = await client.call_tool(
            session,
            "create_lead",
            {
                "name": "Тестовый клиент",
                "contact": "@integration_test",
                "course_id": 1,
                "comment": "Проверка заявки",
            },
        )
        lead = json.loads(raw)
        assert lead["ok"] is True
        assert lead["created"] is True
        assert lead["lead_id"] > 0
