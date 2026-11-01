"""Базовые тесты без обращения к сети."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import agent.tools.crypto as crypto_tool
import agent.tools.currency as currency_tool
from agent.agent import Agent
from agent.config import SETTINGS
from agent.memory import add_message, clear_history, get_history, get_stats
from agent.tools.calculator import calculate
from agent.tools.file_io import list_directory, read_file, write_file
from agent.tools.http_request import http_request
from agent.tools.page_reader import extract_text
from agent.tools.registry import TOOL_SPECS, execute_tool
from agent.tools.search import _parse_date
from agent.tools.time_utils import format_times
from agent.tools.reminders import (
    _now,
    add_reminder,
    clear_reminders,
    get_due_reminders,
    list_reminders,
    mark_reminder_sent,
)
from agent.tools.terminal import run_terminal_command


class AgentToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old = {
            "memory_file": SETTINGS.memory_file,
            "reminders_file": SETTINGS.reminders_file,
            "workspace_dir": SETTINGS.workspace_dir,
        }
        root = Path(cls._tmp.name)
        SETTINGS.memory_file = root / "memory.json"
        SETTINGS.reminders_file = root / "reminders.json"
        SETTINGS.workspace_dir = root / "workspace"
        SETTINGS.workspace_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls) -> None:
        for key, value in cls._old.items():
            setattr(SETTINGS, key, value)
        cls._tmp.cleanup()

    def setUp(self) -> None:
        clear_history()
        clear_history("chat-1")
        clear_history("chat-2")
        clear_reminders(None)
        for item in SETTINGS.workspace_dir.iterdir():
            if item.is_file():
                item.unlink()

    def test_calculator(self) -> None:
        self.assertEqual(calculate("(2+3)*sqrt(16)"), 20.0)
        self.assertEqual(calculate("2**10"), 1024.0)
        self.assertAlmostEqual(calculate("sin(pi/2)"), 1.0)
        with self.assertRaises(ValueError):
            calculate("__import__('os').system('dir')")

    def test_page_text_extraction(self) -> None:
        html = """
        <html>
          <head>
            <title>Новости за 31 октября</title>
            <style>.hidden { color: red; }</style>
          </head>
          <body>
            <script>alert('не текст');</script>
            <h1>Польша</h1>
            <p>В Варшаве протесты у Конституционного суда.</p>
          </body>
        </html>
        """
        title, text = extract_text(html)
        self.assertIn("Новости", title)
        self.assertIn("Польша", text)
        self.assertIn("протесты", text)
        self.assertNotIn("alert", text)
        self.assertNotIn("color", text)

    def test_news_intent_forces_search(self) -> None:
        self.assertTrue(Agent._needs_web_search("Найди новости о 3 ключевых событиях"))
        self.assertTrue(Agent._needs_web_search("найди в duckduckgo новости мира"))
        self.assertTrue(Agent._needs_web_search("Что произошло в мире сегодня?"))
        self.assertTrue(
            Agent._needs_web_search(
                "Найди 3 ключевых события в мире за 31.10.2026, которые произошли вчера"
            )
        )
        self.assertFalse(Agent._needs_web_search("Посчитай (2+3)*sqrt(16)"))
        self.assertFalse(Agent._needs_web_search("Прочитай notes.txt"))

    def test_news_date_parsing(self) -> None:
        self.assertEqual(str(_parse_date("31.10.2026")), "2026-10-31")
        self.assertEqual(str(_parse_date("world news October 31 2026")), "2026-10-31")
        self.assertEqual(str(_parse_date("31 октября 2026")), "2026-10-31")

    def test_timezone_clarification(self) -> None:
        self.assertTrue(Agent._needs_timezone("Покажи курс BTC к RUB"))
        self.assertTrue(Agent._needs_timezone("Который час?"))
        self.assertFalse(Agent._needs_timezone("Покажи курс BTC к RUB в МСК"))
        self.assertFalse(Agent._needs_timezone("Который час в UTC?"))
        self.assertFalse(Agent._needs_timezone("Посчитай (2+3)*sqrt(16)"))

        history = [
            {"role": "user", "content": "Покажи курс BTC к RUB"},
            {"role": "assistant", "content": "В каком часовом поясе показать время?"},
        ]
        self.assertTrue(Agent._is_timezone_answer("МСК", history))
        self.assertFalse(Agent._is_timezone_answer("Курс BTC в МСК", history))

        agent = Agent.__new__(Agent)
        reply = agent.run("Покажи курс BTC к RUB", chat_id="test-timezone")
        self.assertIn("часовом поясе", reply.text)
        clear_history("test-timezone")

    def test_time_formatting(self) -> None:
        times = format_times("2026-10-31T22:48:00+00:00")
        self.assertEqual(times["utc"], "31.10.2026 22:48 UTC")
        self.assertEqual(times["msk"], "01.11.2026 01:48 МСК")

    def test_crypto_quote_fallback(self) -> None:
        original = (
            crypto_tool._coingecko,
            crypto_tool._coinbase,
            crypto_tool._blockchain_info,
        )

        def fail(*args, **kwargs):
            raise RuntimeError("429")

        crypto_tool._coingecko = fail
        crypto_tool._coinbase = lambda coin_id, currency: crypto_tool._quote(
            coin_id, currency, 123.0, "Coinbase", "https://coinbase.com"
        )
        crypto_tool._blockchain_info = fail
        try:
            quote = crypto_tool.get_crypto_quote("btc", "rub")
        finally:
            (
                crypto_tool._coingecko,
                crypto_tool._coinbase,
                crypto_tool._blockchain_info,
            ) = original

        self.assertEqual(quote["source"], "Coinbase")
        self.assertEqual(quote["price"], 123.0)
        self.assertIn("fetched_at", quote)
        self.assertIn("fetched_at_msk", quote)

    def test_currency_quote_metadata(self) -> None:
        original = currency_tool.get_json
        currency_tool.get_json = lambda *args, **kwargs: {
            "result": "success",
            "rates": {"RUB": 80.5},
            "time_last_update_utc": "Sat, 31 Oct 2026 00:00:00 +0000",
        }
        try:
            quote = currency_tool.get_currency_quote("USD", "RUB")
        finally:
            currency_tool.get_json = original

        self.assertEqual(quote["rate"], 80.5)
        self.assertEqual(quote["source"], "open.er-api.com")
        self.assertIn("fetched_at", quote)

    def test_file_sandbox(self) -> None:
        relative = write_file("notes/test.txt", "привет")
        self.assertEqual(Path(relative), Path("notes/test.txt"))
        self.assertEqual(read_file("notes/test.txt"), "привет")
        names = [item["name"] for item in list_directory("notes")]
        self.assertIn("test.txt", names)

        with self.assertRaises(ValueError):
            read_file("../../.env")
        with self.assertRaises(ValueError):
            write_file(str(Path(SETTINGS.workspace_dir).parent / "evil.txt"), "x")

    def test_memory_per_chat(self) -> None:
        add_message("user", "вопрос 1", "chat-1")
        add_message("assistant", "ответ 1", "chat-1")
        add_message("user", "вопрос 2", "chat-2")

        self.assertEqual(len(get_history("chat-1")), 2)
        self.assertEqual(len(get_history("chat-2")), 1)
        self.assertEqual(get_history("chat-2")[0]["content"], "вопрос 2")
        self.assertEqual(get_stats("chat-1")["total_turns"], 1)

        clear_history("chat-1")
        self.assertEqual(get_history("chat-1"), [])

    def test_reminders(self) -> None:
        item = add_reminder("позвонить", "+1m", chat_id="chat-1")
        self.assertIn(item["id"], [x["id"] for x in list_reminders("chat-1")])
        self.assertEqual(list_reminders("chat-2"), [])

        due = get_due_reminders(_now() + timedelta(minutes=2))
        self.assertTrue(any(x["id"] == item["id"] for x in due))
        self.assertTrue(mark_reminder_sent(item["id"]))
        self.assertFalse(any(x["id"] == item["id"] for x in get_due_reminders(_now() + timedelta(minutes=3))))

    def test_qr_and_registry(self) -> None:
        tool_names = [item["function"]["name"] for item in TOOL_SPECS]
        self.assertIn("read_url", tool_names)

        result = execute_tool("generate_qr", {"text": "https://example.com"})
        self.assertTrue(result["ok"])
        self.assertTrue(result["artifacts"])
        self.assertTrue(Path(result["artifacts"][0]).is_file())

        calc = execute_tool("calculate", {"expression": "6*7"})
        self.assertTrue(calc["ok"])
        self.assertEqual(calc["result"], 42.0)

        unknown = execute_tool("does_not_exist", {})
        self.assertFalse(unknown["ok"])

    def test_safety_guards(self) -> None:
        self.assertEqual(run_terminal_command("echo hi")["stdout"], "hi")
        with self.assertRaises(ValueError):
            run_terminal_command("rm -rf /")
        with self.assertRaises(ValueError):
            run_terminal_command("dir & del file.txt")
        with self.assertRaises(ValueError):
            http_request("http://127.0.0.1/secret")

    def test_telegram_reminder_dispatch(self) -> None:
        from agent.bot import check_reminders

        class FakeBot:
            def __init__(self) -> None:
                self.sent: list[tuple[int, str]] = []

            async def send_message(self, chat_id: int, text: str) -> None:
                self.sent.append((chat_id, text))

        item = add_reminder(
            "проверить бота",
            (_now() - timedelta(minutes=1)).isoformat(),
            chat_id="12345",
        )
        fake_bot = FakeBot()
        asyncio.run(check_reminders(SimpleNamespace(bot=fake_bot)))

        self.assertEqual(len(fake_bot.sent), 1)
        self.assertEqual(fake_bot.sent[0][0], 12345)
        self.assertIn("проверить бота", fake_bot.sent[0][1])
        self.assertNotIn(item["id"], [x["id"] for x in list_reminders("12345")])


if __name__ == "__main__":
    unittest.main()
