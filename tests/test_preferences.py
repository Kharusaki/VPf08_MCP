"""Тесты пользовательских настроек длины ответа."""

from __future__ import annotations

import json

import pytest

from telegram_bot.preferences import (
    PreferencesStore,
    answer_length_tokens,
    normalize_answer_length,
)


def test_default_length_is_normal(tmp_path) -> None:
    store = PreferencesStore(tmp_path / "preferences.json")
    assert store.get_answer_length(1) == "normal"
    assert store.max_tokens_for(1, 650) == 650


def test_length_is_saved_and_restored(tmp_path) -> None:
    path = tmp_path / "preferences.json"
    store = PreferencesStore(path)
    assert store.set_answer_length(1, "длинный") == "long"

    restored = PreferencesStore(path)
    assert restored.get_answer_length(1) == "long"


def test_length_is_isolated_by_chat(tmp_path) -> None:
    store = PreferencesStore(tmp_path / "preferences.json")
    store.set_answer_length(1, "short")
    store.set_answer_length(2, "long")

    assert store.get_answer_length(1) == "short"
    assert store.get_answer_length(2) == "long"
    assert store.get_answer_length(3) == "normal"


def test_length_aliases(tmp_path) -> None:
    store = PreferencesStore(tmp_path / "preferences.json")
    assert store.set_answer_length(1, "кратко") == "short"
    assert store.set_answer_length(1, "стандарт") == "normal"
    assert store.set_answer_length(1, "подробно") == "long"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("short", "short"),
        ("короткий", "short"),
        ("normal", "normal"),
        ("обычно", "normal"),
        ("long", "long"),
        ("длинный", "long"),
    ],
)
def test_normalize_answer_length(value: str, expected: str) -> None:
    assert normalize_answer_length(value) == expected


def test_unknown_length_is_rejected(tmp_path) -> None:
    store = PreferencesStore(tmp_path / "preferences.json")
    with pytest.raises(ValueError):
        store.set_answer_length(1, "огромный")


def test_token_limits_by_mode() -> None:
    assert answer_length_tokens("short", 650) == 350
    assert answer_length_tokens("normal", 650) == 650
    assert answer_length_tokens("long", 650) == 1600
    assert answer_length_tokens("long", 2000) == 2000
    assert answer_length_tokens("short", 200) == 200


def test_broken_file_is_reset(tmp_path) -> None:
    path = tmp_path / "preferences.json"
    path.write_text("{это не json", encoding="utf-8")
    store = PreferencesStore(path)
    assert store.get_answer_length(1) == "normal"


def test_file_has_expected_structure(tmp_path) -> None:
    path = tmp_path / "preferences.json"
    store = PreferencesStore(path)
    store.set_answer_length(10, "long")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["chats"]["10"]["answer_length"] == "long"
