"""Тесты чистой логики расчёта цены."""

import pytest

from mcp_server.pricing import PricingError, calculate_price


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
