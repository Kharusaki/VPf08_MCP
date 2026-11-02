"""Чистый расчёт стоимости курса.

Модуль не знает про MCP и SQLite — его легко тестировать отдельно. Это же
свойство помогает не смешивать бизнес-правила с транспортом.
"""

from __future__ import annotations

from dataclasses import dataclass

# Наценка за рассрочку. Сроки, которых нет в словаре, считаются недоступными.
INSTALLMENT_MARKUP: dict[int, int] = {3: 0, 6: 5, 12: 10}


class PricingError(ValueError):
    """Ошибка входных данных расчёта (её можно показать пользователю)."""


@dataclass(frozen=True)
class PriceBreakdown:
    """Результат расчёта, который инструмент отдаёт модели."""

    base_price: int
    discount_percent: int
    discount_rub: int
    subtotal: int
    installment_months: int
    installment_markup_percent: int
    total: int
    monthly_payment: int | None


def calculate_price(
    base_price: int,
    discount_percent: int = 0,
    installment_months: int = 0,
) -> PriceBreakdown:
    """Рассчитать итоговую цену с промокодом и рассрочкой.

    Порядок важен: сначала скидка уменьшает базовую цену, затем на остаток
    начисляется наценка за рассрочку. Так клиент не платит проценты на сумму,
    которую и так покрывает скидка.
    """
    if base_price <= 0:
        raise PricingError("Цена курса должна быть положительной.")
    if not 0 <= discount_percent <= 100:
        raise PricingError("Скидка должна быть в диапазоне от 0 до 100%.")
    if installment_months not in {0, *INSTALLMENT_MARKUP}:
        allowed = ", ".join(str(value) for value in sorted(INSTALLMENT_MARKUP))
        raise PricingError(f"Рассрочка доступна на {allowed} месяцев.")

    discount_rub = round(base_price * discount_percent / 100)
    subtotal = base_price - discount_rub
    markup_percent = INSTALLMENT_MARKUP.get(installment_months, 0)
    total = round(subtotal * (1 + markup_percent / 100))
    monthly = round(total / installment_months) if installment_months else None

    return PriceBreakdown(
        base_price=base_price,
        discount_percent=discount_percent,
        discount_rub=discount_rub,
        subtotal=subtotal,
        installment_months=installment_months,
        installment_markup_percent=markup_percent,
        total=total,
        monthly_payment=monthly,
    )
