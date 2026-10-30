"""Инструмент «Курс обычных валют» через бесплатный open.er-api.com."""

from __future__ import annotations

import logging

from .http_utils import get_json

logger = logging.getLogger(__name__)

RATES_URL = "https://open.er-api.com/v6/latest/{base}"


def get_currency_rate(base: str, target: str) -> float:
    """Вернуть курс: сколько единиц target стоит 1 единица base.

    Пример: get_currency_rate("USD", "RUB") -> 80.57
    """
    base = (base or "").strip().upper()
    target = (target or "").strip().upper()

    for code in (base, target):
        if not code.isalpha() or len(code) != 3:
            raise ValueError("Код валюты должен состоять из 3 букв, например USD или RUB.")

    if base == target:
        return 1.0

    data = get_json(RATES_URL.format(base=base), timeout=20)
    if data.get("result") != "success":
        raise ValueError(data.get("error-type") or "Сервис курсов вернул ошибку.")

    rates = data.get("rates") or {}
    rate = rates.get(target)
    if rate is None:
        raise ValueError(f"Курс {base}/{target} не найден.")
    return float(rate)
