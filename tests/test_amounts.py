"""
Unit tests for Amount Extraction from text (amounts.py).
"""

from decimal import Decimal

from app.services.amounts import extract_amount_rupees


def test_digit_amounts():
    assert extract_amount_rupees("500") == Decimal("500")
    assert extract_amount_rupees("₹1,200") == Decimal("1200")
    assert extract_amount_rupees("500 rupees") == Decimal("500")
    assert extract_amount_rupees("5k") == Decimal("5000")
    assert extract_amount_rupees("500k") == Decimal("500000")
    assert extract_amount_rupees("500 k") == Decimal("500000")
    assert extract_amount_rupees("RAMU 500 KAM") == Decimal("500")
    assert extract_amount_rupees("500 KAM KR DO") == Decimal("500")
    assert extract_amount_rupees("2 lakh") == Decimal("200000")
    assert extract_amount_rupees("1.5 crore") == Decimal("15000000")
    assert extract_amount_rupees("Ramu 24 ka 500 udhaar") == Decimal("500")
    assert extract_amount_rupees("customer 2 ko ₹500") == Decimal("500")
    assert extract_amount_rupees("Ramu ko 500 udhaar 2 din baad") == Decimal("500")
    assert extract_amount_rupees("Ramu ko 500 aur 2 din baad") is None


def test_hindi_word_amounts():
    assert extract_amount_rupees("paanch sau") == Decimal("500")
    assert extract_amount_rupees("do hazaar") == Decimal("2000")
    assert extract_amount_rupees("dhai sau") == Decimal("250")
    assert extract_amount_rupees("sawa lakh") == Decimal("125000")
    assert extract_amount_rupees("paanch sau bees") == Decimal("520")
    assert extract_amount_rupees("bees hazaar pachaas") == Decimal("20050")


def test_empty_or_invalid():
    assert extract_amount_rupees("") is None
    assert extract_amount_rupees("hello world") is None
    assert extract_amount_rupees("Ramu ka hisaab do") is None
    assert extract_amount_rupees("poora hisaab do") is None
    assert extract_amount_rupees("9876543210 ko 500 udhaar") == Decimal("500")
    assert extract_amount_rupees("do rupaye") == Decimal("2")
