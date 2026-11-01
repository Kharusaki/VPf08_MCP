"""Форматирование времени для ответов инструментов."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Optional

MSK = timezone(timedelta(hours=3))


def parse_datetime(value: Any) -> Optional[datetime]:
    """Разобрать ISO-строку, RFC-дату или Unix-время."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)

    text = str(value).strip()
    if not text:
        return None

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_times(value: Any) -> Dict[str, Optional[str]]:
    """Вернуть время в UTC и МСК в человекочитаемом виде."""
    parsed = parse_datetime(value)
    if parsed is None:
        return {"utc": None, "msk": None}
    return {
        "utc": parsed.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
        "msk": parsed.astimezone(MSK).strftime("%d.%m.%Y %H:%M МСК"),
    }
