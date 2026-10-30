"""Долговременная память агента (memory.json).

История хранится отдельно для каждого Telegram-чата, чтобы пользователи
не видели контекст друг друга. Для CLI используется общая история.

Формат файла:
{
    "history": [ ... ],                 # история CLI
    "chats": {
        "123456789": {"history": [...], "metadata": {...}}
    },
    "metadata": {"total_turns": 1, "last_updated": "..."}
}
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import SETTINGS

# Блокировка нужна, чтобы бот и CLI не перезаписали файл одновременно
_LOCK = threading.RLock()


def _empty_bucket() -> Dict[str, Any]:
    """Пустая история для одного чата."""
    return {"history": [], "metadata": {"total_turns": 0}}


def _empty_data() -> Dict[str, Any]:
    """Пустой файл памяти."""
    return {"history": [], "chats": {}, "metadata": {"total_turns": 0}}


def _load() -> Dict[str, Any]:
    """Загрузить память с диска. Если файла нет — вернуть пустую структуру."""
    path: Path = SETTINGS.memory_file
    if not path.exists():
        return _empty_data()

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        # Файл повреждён — начинаем с чистого листа, старый сохраняем рядом
        try:
            path.replace(path.with_suffix(".json.broken"))
        except OSError:
            pass
        return _empty_data()

    if not isinstance(data, dict):
        return _empty_data()

    # Приводим старый/частично заполненный файл к актуальной структуре
    data.setdefault("history", [])
    data.setdefault("chats", {})
    data.setdefault("metadata", {"total_turns": 0})
    if not isinstance(data["history"], list):
        data["history"] = []
    if not isinstance(data["chats"], dict):
        data["chats"] = {}
    if not isinstance(data["metadata"], dict):
        data["metadata"] = {"total_turns": 0}
    return data


def _save(data: Dict[str, Any]) -> None:
    """Атомарно сохранить память на диск."""
    path: Path = SETTINGS.memory_file
    path.parent.mkdir(parents=True, exist_ok=True)
    data["metadata"]["last_updated"] = datetime.now(timezone.utc).isoformat()

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _bucket(data: Dict[str, Any], chat_id: Optional[str]) -> Dict[str, Any]:
    """Вернуть (и при необходимости создать) историю нужного чата."""
    if chat_id is None:
        return {"history": data["history"], "metadata": data["metadata"]}

    key = str(chat_id)
    bucket = data["chats"].get(key)
    if not isinstance(bucket, dict):
        bucket = _empty_bucket()
        data["chats"][key] = bucket
    bucket.setdefault("history", [])
    bucket.setdefault("metadata", {"total_turns": 0})
    if not isinstance(bucket["history"], list):
        bucket["history"] = []
    if not isinstance(bucket["metadata"], dict):
        bucket["metadata"] = {"total_turns": 0}
    return bucket


def _clean_history(history: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Оставить только пользовательские и ответные сообщения."""
    result: List[Dict[str, str]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str):
            result.append({"role": role, "content": content})
    return result


def get_history(chat_id: Optional[str] = None) -> List[Dict[str, str]]:
    """Вернуть историю сообщений (ограниченную по длине)."""
    with _LOCK:
        data = _load()
        history = _clean_history(_bucket(data, chat_id)["history"])
        limit = max(1, SETTINGS.history_limit)
        return history[-limit:]


def add_message(role: str, content: str, chat_id: Optional[str] = None) -> None:
    """Добавить одно сообщение в историю нужного чата."""
    if role not in {"user", "assistant"}:
        raise ValueError("В память можно писать только роли user/assistant")
    if not isinstance(content, str):
        content = str(content)

    with _LOCK:
        data = _load()
        bucket = _bucket(data, chat_id)
        bucket["history"].append({"role": role, "content": content})

        # Храним только последние N сообщений
        limit = max(1, SETTINGS.history_limit)
        bucket["history"] = bucket["history"][-limit:]

        if role == "user":
            bucket["metadata"]["total_turns"] = bucket["metadata"].get("total_turns", 0) + 1

        _save(data)


def clear_history(chat_id: Optional[str] = None) -> None:
    """Очистить историю (для CLI или конкретного чата)."""
    with _LOCK:
        data = _load()
        if chat_id is None:
            data["history"] = []
            data["metadata"]["total_turns"] = 0
        else:
            data["chats"][str(chat_id)] = _empty_bucket()
        _save(data)


def get_stats(chat_id: Optional[str] = None) -> Dict[str, Any]:
    """Вернуть статистику памяти."""
    with _LOCK:
        data = _load()
        bucket = _bucket(data, chat_id)
        return {
            "scope": "cli" if chat_id is None else f"chat {chat_id}",
            "total_turns": bucket["metadata"].get("total_turns", 0),
            "last_updated": data["metadata"].get("last_updated", "never"),
            "message_count": len(_clean_history(bucket["history"])),
            "memory_file": str(SETTINGS.memory_file),
        }
