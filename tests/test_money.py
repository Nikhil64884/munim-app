"""
Unit tests for Money handling (money.py).
"""

from decimal import Decimal

import pytest

from app.money import (
    format_inr,
    paise_to_rupees,
    rupees_to_paise,
    validate_amount_paise,
)


def test_rupees_to_paise():
    assert rupees_to_paise(500) == 50000
    assert rupees_to_paise(500.50) == 50050
    assert rupees_to_paise(0.99) == 99
    assert rupees_to_paise("100.25") == 10025
    assert rupees_to_paise(Decimal("1.005")) == 101
    assert rupees_to_paise(1.005) == 101

    with pytest.raises(TypeError):
        rupees_to_paise(True)


def test_paise_to_rupees():
    assert paise_to_rupees(50000) == 500.0
    assert paise_to_rupees(50050) == 500.5
    assert paise_to_rupees(99) == 0.99

    with pytest.raises(TypeError):
        paise_to_rupees(True)
    with pytest.raises(TypeError):
        paise_to_rupees("500")


def test_format_inr():
    assert format_inr(50000) == "₹500"
    assert format_inr(50050) == "₹500.50"
    assert format_inr(50000, always_decimals=True) == "₹500.00"
    assert format_inr(123456789) == "₹12,34,567.89"
    assert format_inr(-50000) == "-₹500"
    assert format_inr(50000, with_symbol=False) == "500"


def test_validate_amount_paise():
    ok, _ = validate_amount_paise(50000)
    assert ok is True

    # Rejection cases
    ok, reason = validate_amount_paise(True)
    assert ok is False
    assert "boolean" in reason.lower()

    ok, reason = validate_amount_paise(0)
    assert ok is False
    assert "zero" in reason.lower()

    ok, reason = validate_amount_paise(-100)
    assert ok is False
    assert "negative" in reason.lower()

    ok, reason = validate_amount_paise(10_000_000 * 100 + 1)
    assert ok is False
    assert "exceeds max" in reason.lower()
