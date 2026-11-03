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


@dataclass(frozen=True)
class OrderLine:
    """Одна позиция заказа: курс и его цена с учётом скидки."""

    course_id: int
    title: str
    base_price: int
    discount_percent: int
    discount_rub: int
    total: int


@dataclass(frozen=True)
class OrderBreakdown:
    """Заказ из нескольких курсов, где скидка применена ровно к одному."""

    lines: tuple[OrderLine, ...]
    promo_code: str | None
    discounted_course_id: int | None
    total: int
    options: tuple[dict[str, int], ...]


def calculate_order(
    courses: list[tuple[int, str, int]],
    promo_code: str = "",
    discount_percent: int = 0,
    apply_to_course_id: int = 0,
) -> OrderBreakdown:
    """Рассчитать заказ из нескольких курсов.

    Промокод центра действует ровно на один курс, поэтому складывать скидку
    ко всем позициям нельзя. Расчёт делает сервер, а не модель: так итог заказа
    не зависит от арифметики в тексте ответа.

    Args:
        courses: список ``(id, название, цена)``.
        promo_code: код промоакции для ответа; пустая строка — без скидки.
        discount_percent: процент скидки; 0 — считать без скидки.
        apply_to_course_id: курс, к которому применить скидку.
            0 — выбрать самый дорогой (на нём скидка даёт наибольшую выгоду).
    """
    if not courses:
        raise PricingError("Укажите хотя бы один курс.")

    percent = int(discount_percent)
    if not 0 <= percent <= 100:
        raise PricingError("Скидка должна быть в диапазоне от 0 до 100%.")

    # Скидка в рублях тем больше, чем дороже курс: выбираем максимальную цену.
    if apply_to_course_id:
        target = next(
            (course for course in courses if course[0] == int(apply_to_course_id)),
            None,
        )
        if target is None:
            raise PricingError("Курс из заказа не найден в каталоге.")
    else:
        target = max(courses, key=lambda course: (course[2], -course[0]))

    lines: list[OrderLine] = []
    for course_id, title, price in courses:
        if price <= 0:
            raise PricingError("Цена курса должна быть положительной.")
        apply = bool(percent) and course_id == target[0]
        line_discount = round(price * percent / 100) if apply else 0
        lines.append(
            OrderLine(
                course_id=course_id,
                title=title,
                base_price=int(price),
                discount_percent=percent if apply else 0,
                discount_rub=line_discount,
                total=int(price) - line_discount,
            )
        )

    # Варианты: что получится, если скидку применить к каждому из курсов.
    options: list[dict[str, int]] = []
    for course_id, _title, price in courses:
        total_without = sum(int(item[2]) for item in courses)
        options.append(
            {
                "course_id": course_id,
                "order_total": total_without - round(price * percent / 100),
                "savings": round(price * percent / 100),
            }
        )

    return OrderBreakdown(
        lines=tuple(lines),
        promo_code=(promo_code or "").strip().upper() or None,
        discounted_course_id=target[0] if percent else None,
        total=sum(line.total for line in lines),
        options=tuple(options),
    )
