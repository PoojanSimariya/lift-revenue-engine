"""Unit tests for integer subunit Money representation and currency arithmetic."""

import pytest
from lift.core.constants import INT64_MAX, INT64_MIN
from lift.core.errors import CurrencyMismatchError, DataValidationError
from lift.core.money import Money


def test_money_creation_valid() -> None:
    m = Money(amount_subunits=1000, currency="INR")
    assert m.amount_subunits == 1000
    assert m.currency == "INR"
    assert m.is_positive
    assert not m.is_zero
    assert not m.is_negative


def test_money_helpers() -> None:
    m1 = Money.from_paise(500)
    assert m1.amount_subunits == 500
    assert m1.currency == "INR"

    m2 = Money.from_rupees_integer(50)
    assert m2.amount_subunits == 5000
    assert m2.currency == "INR"

    z = Money.zero("USD")
    assert z.amount_subunits == 0
    assert z.currency == "USD"
    assert z.is_zero


def test_money_rejects_floats() -> None:
    with pytest.raises(DataValidationError):
        Money(amount_subunits=100.5)  # type: ignore[arg-type]

    with pytest.raises(DataValidationError):
        Money.from_rupees_integer(10.5)  # type: ignore[arg-type]


def test_money_rejects_booleans() -> None:
    with pytest.raises(DataValidationError):
        Money(amount_subunits=True)  # type: ignore[arg-type]

    with pytest.raises(DataValidationError):
        Money(amount_subunits=False)  # type: ignore[arg-type]


def test_money_int64_boundaries() -> None:
    # Within limits
    m_max = Money(amount_subunits=INT64_MAX)
    assert m_max.amount_subunits == INT64_MAX

    m_min = Money(amount_subunits=INT64_MIN)
    assert m_min.amount_subunits == INT64_MIN

    # Out of limits
    with pytest.raises(DataValidationError):
        Money(amount_subunits=INT64_MAX + 1)

    with pytest.raises(DataValidationError):
        Money(amount_subunits=INT64_MIN - 1)


def test_money_currency_validation() -> None:
    m = Money(amount_subunits=100, currency="inr")
    assert m.currency == "INR"

    with pytest.raises(DataValidationError):
        Money(amount_subunits=100, currency="IN")

    with pytest.raises(DataValidationError):
        Money(amount_subunits=100, currency="INDIA")


def test_money_addition_and_subtraction() -> None:
    m1 = Money(amount_subunits=1000, currency="INR")
    m2 = Money(amount_subunits=500, currency="INR")

    add_res = m1 + m2
    assert add_res.amount_subunits == 1500
    assert add_res.currency == "INR"

    sub_res = m1 - m2
    assert sub_res.amount_subunits == 500
    assert sub_res.currency == "INR"


def test_money_currency_mismatch_raises() -> None:
    inr = Money(amount_subunits=1000, currency="INR")
    usd = Money(amount_subunits=1000, currency="USD")

    with pytest.raises(CurrencyMismatchError):
        _ = inr + usd

    with pytest.raises(CurrencyMismatchError):
        _ = inr - usd

    with pytest.raises(CurrencyMismatchError):
        _ = inr < usd


def test_money_multiplication_and_division() -> None:
    m = Money(amount_subunits=1000, currency="INR")

    # Integer scalar multiplication
    res_mul = m * 3
    assert res_mul.amount_subunits == 3000

    res_rmul = 2 * m
    assert res_rmul.amount_subunits == 2000

    # Float multiplication rejected
    with pytest.raises(DataValidationError):
        _ = m * 1.5  # type: ignore[operator]

    with pytest.raises(DataValidationError):
        _ = m * True  # type: ignore[operator]

    # Integer floor division
    res_div = m // 3
    assert res_div.amount_subunits == 333

    with pytest.raises(ZeroDivisionError):
        _ = m // 0


def test_money_comparisons() -> None:
    m1 = Money(amount_subunits=100, currency="INR")
    m2 = Money(amount_subunits=200, currency="INR")
    m3 = Money(amount_subunits=100, currency="INR")

    assert m1 < m2
    assert m1 <= m2
    assert m1 <= m3
    assert m2 > m1
    assert m2 >= m1
    assert m1 == m3
    assert m1 != m2
    assert m1 != "100 INR"


def test_money_negation_and_abs() -> None:
    m = Money(amount_subunits=100, currency="INR")
    neg_m = -m
    assert neg_m.amount_subunits == -100
    assert neg_m.is_negative

    abs_m = abs(neg_m)
    assert abs_m.amount_subunits == 100
    assert abs_m.is_positive

    pos_m = +m
    assert pos_m.amount_subunits == 100

    assert m.as_subunits() == 100
    assert "100 INR subunits" in str(m)
    assert "Money(amount_subunits=100, currency='INR')" in repr(m)


def test_money_invalid_operands() -> None:
    m = Money(amount_subunits=100, currency="INR")

    with pytest.raises(TypeError):
        _ = m + 100  # type: ignore[operator]

    with pytest.raises(TypeError):
        _ = m - 100  # type: ignore[operator]

    with pytest.raises(TypeError):
        _ = m < 100  # type: ignore[operator]

    with pytest.raises(TypeError):
        _ = m <= 100  # type: ignore[operator]

    with pytest.raises(TypeError):
        _ = m > 100  # type: ignore[operator]

    with pytest.raises(TypeError):
        _ = m >= 100  # type: ignore[operator]

    with pytest.raises(DataValidationError):
        _ = m // 2.5  # type: ignore[operator]

    with pytest.raises(DataValidationError):
        _ = m // True  # type: ignore[operator]
