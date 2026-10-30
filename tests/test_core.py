"""Базовые тесты без обращения к сети."""

from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from agent.config import SETTINGS
from agent.memory import add_message, clear_history, get_history, get_stats
from agent.tools.calculator import calculate
from agent.tools.file_io import list_directory, read_file, write_file
from agent.tools.http_request import http_request
from agent.tools.registry import execute_tool
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


if __name__ == "__main__":
    unittest.main()
