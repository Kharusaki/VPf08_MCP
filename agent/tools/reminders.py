"""Инструмент «Напоминания»: хранит задачи в reminders.json.

Бот периодически проверяет файл и присылает напоминание в нужный чат.
CLI умеет добавлять и смотреть напоминания, но не присылает их сам.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import SETTINGS

_LOCK = threading.RLock()


def _now() -> datetime:
    """Текущее локальное время с часовым поясом."""
    return datetime.now().astimezone()


def _load() -> Dict[str, Any]:
    """Прочитать файл напоминаний."""
    path: Path = SETTINGS.reminders_file
    if not path.exists():
        return {"reminders": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"reminders": []}
    if not isinstance(data, dict) or not isinstance(data.get("reminders"), list):
        return {"reminders": []}
    return data


def _save(data: Dict[str, Any]) -> None:
    """Атомарно сохранить напоминания."""
    path: Path = SETTINGS.reminders_file
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _parse_when(when: str) -> datetime:
    """Понять дату/время из ISO, 'YYYY-MM-DD HH:MM' или '+10m'."""
    raw = (when or "").strip()
    if not raw:
        raise ValueError("Не указано время напоминания.")

    # Относительное время: +10m, +2h, +1d
    match = re.fullmatch(r"\+(\d+)\s*([mhd])", raw.lower())
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        delta = {"m": timedelta(minutes=amount), "h": timedelta(hours=amount), "d": timedelta(days=amount)}[unit]
        return _now() + delta

    # ISO с часовым поясом или без
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed
    except ValueError:
        pass

    # Человеческие форматы
    formats = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
        "%Y-%m-%d",
        "%H:%M",
    ]
    for fmt in formats:
        try:
            parsed = datetime.strptime(raw, fmt)
        except ValueError:
            continue

        now = _now()
        if fmt == "%H:%M":
            parsed = now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
            if parsed < now:
                parsed += timedelta(days=1)
        elif fmt in {"%Y-%m-%d", "%d.%m.%Y"}:
            parsed = parsed.replace(hour=9, minute=0)
        return parsed.astimezone()

    raise ValueError(
        "Не понял время. Используйте ISO 8601 (2026-10-31T18:00), "
        "формат 2026-10-31 18:00 или +30m / +2h / +1d."
    )


def _public(item: Dict[str, Any]) -> Dict[str, Any]:
    """Оставить только нужные поля для ответа пользователю."""
    return {
        "id": item.get("id"),
        "text": item.get("text"),
        "when": item.get("when"),
        "chat_id": item.get("chat_id"),
        "sent": bool(item.get("sent")),
    }


def add_reminder(text: str, when: str, chat_id: str = "cli") -> Dict[str, Any]:
    """Добавить напоминание и вернуть его описание."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Не указан текст напоминания.")
    if len(text) > 500:
        raise ValueError("Текст напоминания слишком длинный (максимум 500 символов).")

    due = _parse_when(when)
    item = {
        "id": uuid.uuid4().hex[:8],
        "text": text,
        "when": due.isoformat(),
        "chat_id": str(chat_id),
        "created": _now().isoformat(),
        "sent": False,
    }

    with _LOCK:
        data = _load()
        data["reminders"].append(item)
        _save(data)
    return _public(item)


def list_reminders(chat_id: Optional[str] = None, include_sent: bool = False) -> List[Dict[str, Any]]:
    """Показать напоминания (по умолчанию только активные)."""
    with _LOCK:
        data = _load()

    items = data["reminders"]
    if chat_id is not None:
        items = [item for item in items if str(item.get("chat_id")) == str(chat_id)]
    if not include_sent:
        items = [item for item in items if not item.get("sent")]
    items = sorted(items, key=lambda item: str(item.get("when", "")))
    return [_public(item) for item in items]


def clear_reminders(chat_id: Optional[str] = None) -> int:
    """Удалить напоминания (все или только для одного чата)."""
    with _LOCK:
        data = _load()
        before = len(data["reminders"])
        if chat_id is None:
            data["reminders"] = []
        else:
            data["reminders"] = [
                item for item in data["reminders"] if str(item.get("chat_id")) != str(chat_id)
            ]
        _save(data)
        return before - len(data["reminders"])


def get_due_reminders(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Вернуть напоминания, время которых уже наступило."""
    current = now or _now()
    with _LOCK:
        data = _load()

    due: List[Dict[str, Any]] = []
    for item in data["reminders"]:
        if item.get("sent"):
            continue
        try:
            when = datetime.fromisoformat(str(item.get("when")))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.astimezone()
        if when <= current:
            due.append(_public(item))
    return due


def mark_reminder_sent(reminder_id: str) -> bool:
    """Отметить напоминание отправленным."""
    with _LOCK:
        data = _load()
        for item in data["reminders"]:
            if item.get("id") == reminder_id:
                item["sent"] = True
                item["sent_at"] = _now().isoformat()
                _save(data)
                return True
    return False
