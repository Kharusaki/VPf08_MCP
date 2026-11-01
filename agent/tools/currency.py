"""Инструмент «Курс обычных валют» через бесплатный open.er-api.com."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from .http_utils import get_json
from .time_utils import format_times

logger = logging.getLogger(__name__)

RATES_URL = "https://open.er-api.com/v6/latest/{base}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_currency_quote(base: str, target: str) -> Dict[str, Any]:
    """Вернуть курс, источник и время обновления."""
    base = (base or "").strip().upper()
    target = (target or "").strip().upper()

    for code in (base, target):
        if not code.isalpha() or len(code) != 3:
            raise ValueError("Код валюты должен состоять из 3 букв, например USD или RUB.")

    if base == target:
        fetched_at = _now_iso()
        fetched_times = format_times(fetched_at)
        return {
            "base": base,
            "target": target,
            "rate": 1.0,
            "source": "расчет",
            "source_url": None,
            "updated_at": None,
            "updated_at_utc": None,
            "updated_at_msk": None,
            "fetched_at": fetched_at,
            "fetched_at_utc": fetched_times["utc"],
            "fetched_at_msk": fetched_times["msk"],
        }

    data = get_json(RATES_URL.format(base=base), timeout=20)
    if data.get("result") != "success":
        raise ValueError(data.get("error-type") or "Сервис курсов вернул ошибку.")

    rates = data.get("rates") or {}
    rate = rates.get(target)
    if rate is None:
        raise ValueError(f"Курс {base}/{target} не найден.")
    updated_at = data.get("time_last_update_utc")
    fetched_at = _now_iso()
    fetched_times = format_times(fetched_at)
    updated_times = format_times(updated_at)
    return {
        "base": base,
        "target": target,
        "rate": float(rate),
        "source": "open.er-api.com",
        "source_url": "https://open.er-api.com/",
        "updated_at": updated_at,
        "updated_at_utc": updated_times["utc"],
        "updated_at_msk": updated_times["msk"],
        "fetched_at": fetched_at,
        "fetched_at_utc": fetched_times["utc"],
        "fetched_at_msk": fetched_times["msk"],
    }


def get_currency_rate(base: str, target: str) -> float:
    """Вернуть только числовой курс (совместимость со старым кодом)."""
    return float(get_currency_quote(base, target)["rate"])
