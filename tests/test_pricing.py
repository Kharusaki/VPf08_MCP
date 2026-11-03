"""Тесты чистой логики расчёта цены."""

import pytest

from mcp_server.pricing import (
    PricingError,
    calculate_order,
    calculate_price,
)

ORDER = [(5, "Немецкий с нуля", 17990), (3, "Бизнес-английский", 29990)]


def test_full_payment_without_discount() -> None:
    result = calculate_price(14990)
    assert result.discount_rub == 0
    assert result.total == 14990
    assert result.monthly_payment is None


def test_promo_discount() -> None:
    result = calculate_price(14990, discount_percent=10)
    assert result.discount_rub == 1499
    assert result.total == 13491


def test_installment_markup_applies_after_discount() -> None:
    result = calculate_price(10000, discount_percent=10, installment_months=6)
    assert result.subtotal == 9000
    assert result.installment_markup_percent == 5
    assert result.total == 9450
    assert result.monthly_payment == 1575


def test_twelve_month_installment() -> None:
    result = calculate_price(30000, installment_months=12)
    assert result.total == 33000
    assert result.monthly_payment == 2750


@pytest.mark.parametrize("months", [1, 2, 5, 24])
def test_invalid_installment_period(months: int) -> None:
    with pytest.raises(PricingError):
        calculate_price(10000, installment_months=months)


def test_invalid_base_price() -> None:
    with pytest.raises(PricingError):
        calculate_price(0)


def test_order_without_promo_is_sum_of_prices() -> None:
    order = calculate_order(ORDER)
    assert [line.total for line in order.lines] == [17990, 29990]
    assert order.total == 47980
    assert order.discounted_course_id is None


def test_order_promo_applies_to_most_expensive_course_only() -> None:
    order = calculate_order(ORDER, promo_code="START10", discount_percent=10)
    # Скидка 10% только с дорогого курса: 2999, а не с обоих (4798).
    assert order.discounted_course_id == 3
    assert [line.discount_rub for line in order.lines] == [0, 2999]
    assert order.total == 44981


def test_order_promo_can_be_applied_to_chosen_course() -> None:
    order = calculate_order(
        ORDER,
        promo_code="START10",
        discount_percent=10,
        apply_to_course_id=5,
    )
    assert order.discounted_course_id == 5
    assert [line.discount_rub for line in order.lines] == [1799, 0]
    assert order.total == 46181


def test_order_options_show_savings_per_course() -> None:
    order = calculate_order(ORDER, promo_code="START10", discount_percent=10)
    assert {item["course_id"] for item in order.options} == {3, 5}
    assert min(item["order_total"] for item in order.options) == order.total


def test_order_requires_courses() -> None:
    with pytest.raises(PricingError):
        calculate_order([])


def test_order_unknown_course_raises() -> None:
    with pytest.raises(PricingError):
        calculate_order(ORDER, apply_to_course_id=99)
