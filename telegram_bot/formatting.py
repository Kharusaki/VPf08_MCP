"""Форматирование ответов для Telegram."""

from __future__ import annotations

TELEGRAM_SAFE_LIMIT = 3900


def split_text(text: str, limit: int = TELEGRAM_SAFE_LIMIT) -> list[str]:
    """Разбить длинный текст на части, не разрывая слова без необходимости."""
    value = (text or "").strip()
    if not value:
        return []
    if len(value) <= limit:
        return [value]

    chunks: list[str] = []
    while len(value) > limit:
        cut = value.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = value.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(value[:cut].rstrip())
        value = value[cut:].lstrip()
    if value:
        chunks.append(value)
    return chunks


def money(value: float) -> str:
    """Отформатировать цену в рублях: 14990 -> «14 990 ₽»."""
    return f"{int(value):,}".replace(",", " ") + " ₽"
