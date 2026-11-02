"""Краткосрочная память диалога.

История хранится отдельно для каждого чата и ограничена и по числу сообщений,
и по суммарному размеру. Это защищает контекст модели от бесконтрольного роста
и снижает расход токенов.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class HistoryStore:
    """Потокобезопасное JSON-хранилище последних сообщений по чатам."""

    def __init__(
        self,
        path: str | Path,
        limit: int = 12,
        max_chars: int = 9000,
        max_message_chars: int = 4000,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        self.limit = max(2, int(limit))
        self.max_chars = max(1000, int(max_chars))
        self.max_message_chars = max(500, int(max_message_chars))
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
                raise ValueError("Неверная структура файла истории")
            self._data = {"chats": raw["chats"]}
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("История повреждена и будет сброшена: %s", exc)
            self._data = {"chats": {}}
        return self._data

    def _save(self) -> None:
        """Атомарно записать историю, чтобы не оставить полусохранённый JSON."""
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

    def get(self, chat_id: int | str) -> list[dict[str, str]]:
        """Вернуть копию истории чата в формате сообщений OpenAI."""
        with self._lock:
            data = self._load()
            items = data["chats"].get(self._chat_key(chat_id), [])
            if not isinstance(items, list):
                return []
            return [
                {"role": item["role"], "content": item["content"]}
                for item in items
                if isinstance(item, dict)
                and item.get("role") in {"user", "assistant"}
                and isinstance(item.get("content"), str)
            ]

    def append(self, chat_id: int | str, role: str, content: str) -> None:
        """Добавить сообщение и обрезать историю до лимитов."""
        if role not in {"user", "assistant"}:
            raise ValueError("В историю можно писать только роли user/assistant")
        text = str(content or "").strip()[: self.max_message_chars]
        if not text:
            return

        with self._lock:
            data = self._load()
            key = self._chat_key(chat_id)
            items = data["chats"].setdefault(key, [])
            if not isinstance(items, list):
                items = []
                data["chats"][key] = items
            items.append({"role": role, "content": text})
            self._trim(items)
            self._save()

    def _trim(self, items: list[dict[str, str]]) -> None:
        """Уложиться в лимиты по количеству и суммарной длине сообщений."""
        while len(items) > self.limit:
            items.pop(0)
        while len(items) > 2 and sum(len(item["content"]) for item in items) > self.max_chars:
            items.pop(0)

    def clear(self, chat_id: int | str) -> None:
        """Очистить историю конкретного чата."""
        with self._lock:
            data = self._load()
            data["chats"].pop(self._chat_key(chat_id), None)
            self._save()

    def count(self, chat_id: int | str) -> int:
        """Количество сообщений в истории чата."""
        return len(self.get(chat_id))
