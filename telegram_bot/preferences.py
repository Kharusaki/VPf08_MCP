"""Пользовательские настройки, которые сохраняются между сообщениями.

Сейчас это только длина ответа. Настройки хранятся отдельно от истории, чтобы
команда ``/clear`` очищала диалог, но не сбрасывала выбранный режим.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Пользовательские названия режимов. Ключи — то, что хранится в JSON.
ANSWER_LENGTHS: dict[str, str] = {
    "short": "короткий",
    "normal": "обычный",
    "long": "длинный",
}

# Границы для короткого и длинного режимов. Обычный режим берёт значение
# DEFAULT_MAX_TOKENS из .env, чтобы настройка в одном месте оставалась главной.
SHORT_MAX_TOKENS = 350
LONG_MAX_TOKENS = 1600

_ALIASES: dict[str, str] = {
    "short": "short",
    "короткий": "short",
    "коротко": "short",
    "кратко": "short",
    "s": "short",
    "1": "short",
    "normal": "normal",
    "обычный": "normal",
    "обычно": "normal",
    "стандартный": "normal",
    "стандарт": "normal",
    "n": "normal",
    "2": "normal",
    "long": "long",
    "длинный": "long",
    "длинно": "long",
    "подробный": "long",
    "подробно": "long",
    "l": "long",
    "3": "long",
}


def normalize_answer_length(value: str) -> str:
    """Привести пользовательский ввод к ключу режима.

    Понимает и английские названия, и русские синонимы. При неизвестном
    значении выбрасывает ``ValueError`` с понятным списком вариантов.
    """
    key = (value or "").strip().lower().replace("ё", "е")
    mode = _ALIASES.get(key)
    if mode is None:
        allowed = ", ".join(ANSWER_LENGTHS)
        raise ValueError(f"Неизвестная длина ответа. Доступно: {allowed}.")
    return mode


def answer_length_tokens(mode: str, default_max_tokens: int) -> int:
    """Вернуть лимит токенов для режима.

    ``normal`` использует значение из настроек, ``short`` не поднимает его выше
    350, а ``long`` — не опускает ниже 1600.
    """
    base = max(1, int(default_max_tokens))
    if mode == "short":
        return min(SHORT_MAX_TOKENS, base)
    if mode == "long":
        return max(LONG_MAX_TOKENS, base)
    return base


class PreferencesStore:
    """Потокобезопасное JSON-хранилище настроек по чатам."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self._lock = threading.RLock()
        self._data: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        """Загрузить файл один раз; повреждённый файл заменить пустой структурой."""
        if self._data is not None:
            return self._data
        if not self.path.exists():
            self._data = {"chats": {}}
            return self._data
        try:
            with open(self.path, "r", encoding="utf-8") as file:
                raw = json.load(file)
            if not isinstance(raw, dict) or not isinstance(raw.get("chats"), dict):
                raise ValueError("Неверная структура файла настроек")

            chats: dict[str, dict[str, str]] = {}
            for chat_id, value in raw["chats"].items():
                if not isinstance(value, dict):
                    continue
                mode = value.get("answer_length", "normal")
                if not isinstance(mode, str) or mode not in ANSWER_LENGTHS:
                    mode = "normal"
                chats[str(chat_id)] = {"answer_length": mode}
            self._data = {"chats": chats}
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Настройки повреждены и будут сброшены: %s", exc)
            self._data = {"chats": {}}
        return self._data

    def _save(self) -> None:
        """Атомарно записать настройки, чтобы не оставить полусохранённый JSON."""
        data = self._load()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(temp_path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, separators=(",", ":"))
        os.replace(temp_path, self.path)

    @staticmethod
    def _chat_key(chat_id: int | str) -> str:
        """Привести идентификатор чата к строковому ключу JSON."""
        return str(chat_id)

    def get_answer_length(self, chat_id: int | str) -> str:
        """Вернуть режим длины ответа; по умолчанию — ``normal``."""
        with self._lock:
            data = self._load()
            value = data["chats"].get(self._chat_key(chat_id), {})
            mode = value.get("answer_length", "normal") if isinstance(value, dict) else "normal"
        return mode if mode in ANSWER_LENGTHS else "normal"

    def set_answer_length(self, chat_id: int | str, value: str) -> str:
        """Сохранить режим длины ответа и вернуть его ключ."""
        mode = normalize_answer_length(value)
        with self._lock:
            data = self._load()
            key = self._chat_key(chat_id)
            data["chats"].setdefault(key, {})["answer_length"] = mode
            self._save()
        return mode

    def max_tokens_for(self, chat_id: int | str, default_max_tokens: int) -> int:
        """Вернуть лимит ответа с учётом режима конкретного чата."""
        return answer_length_tokens(
            self.get_answer_length(chat_id),
            default_max_tokens,
        )
