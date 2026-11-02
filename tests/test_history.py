"""Тесты краткосрочной памяти диалога."""

from __future__ import annotations

import json

from telegram_bot.history import HistoryStore


def test_history_limits_messages(tmp_path) -> None:
    store = HistoryStore(tmp_path / "history.json", limit=4, max_chars=10000)
    for index in range(6):
        role = "user" if index % 2 == 0 else "assistant"
        store.append(42, role, f"сообщение {index}")

    history = store.get(42)
    assert len(history) == 4
    assert history[0]["content"] == "сообщение 2"
    assert history[-1]["content"] == "сообщение 5"


def test_history_survives_reload(tmp_path) -> None:
    path = tmp_path / "history.json"
    store = HistoryStore(path, limit=10)
    store.append(1, "user", "Привет")
    store.append(1, "assistant", "Здравствуйте")

    restored = HistoryStore(path, limit=10)
    assert restored.get(1) == [
        {"role": "user", "content": "Привет"},
        {"role": "assistant", "content": "Здравствуйте"},
    ]


def test_history_is_isolated_by_chat(tmp_path) -> None:
    store = HistoryStore(tmp_path / "history.json")
    store.append(1, "user", "Первый чат")
    store.append(2, "user", "Второй чат")

    assert store.get(1)[0]["content"] == "Первый чат"
    assert store.get(2)[0]["content"] == "Второй чат"


def test_history_clear(tmp_path) -> None:
    store = HistoryStore(tmp_path / "history.json")
    store.append(1, "user", "Текст")
    store.clear(1)
    assert store.get(1) == []
    assert store.count(1) == 0


def test_broken_file_is_reset(tmp_path) -> None:
    path = tmp_path / "history.json"
    path.write_text("{это не json", encoding="utf-8")
    store = HistoryStore(path)
    assert store.get(1) == []


def test_file_has_expected_structure(tmp_path) -> None:
    path = tmp_path / "history.json"
    store = HistoryStore(path)
    store.append(10, "user", "Привет")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["chats"]["10"][0]["content"] == "Привет"
