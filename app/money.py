"""
Money utilities — everything stored as INTEGER PAISE.

Formatting produces Indian style:
    ₹1,23,456         (no decimals when whole)
    ₹1,23,456.78      (with paise)
    ₹1,23,456.00      (always_decimals=True)

Note: bool is a subclass of int in Python, so we explicitly reject it
      in validate_amount_paise() to prevent silent bugs.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.config import config

MAX_PAISE = config.MAX_AMOUNT_RUPEES * 100


# ---------------------------------------------------------------------------
# Conversions
# ---------------------------------------------------------------------------
def rupees_to_paise(rupees: Decimal | float | int | str) -> int:
    """Convert rupees to integer paise, rounding half-up to the nearest paisa."""
    if isinstance(rupees, bool):
        raise TypeError("rupees cannot be a boolean.")
    try:
        amount = Decimal(str(rupees))
    except (InvalidOperation, ValueError):
        raise ValueError(f"Invalid rupee amount: {rupees!r}") from None
    if not amount.is_finite():
        raise ValueError("rupees must be a finite number.")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def paise_to_rupees(paise: int) -> float:
    """Convert paise back to rupees (float). For display / external APIs only."""
    _ensure_int_paise(paise)
    return paise / 100.0


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
def format_inr(
    paise: int,
    with_symbol: bool = True,
    always_decimals: bool = False,
) -> str:
    """
    Format paise as Indian-style rupee string.

    Args:
        paise: Amount in paise (integer).
        with_symbol: Include the ₹ symbol.
        always_decimals: Force two decimal places (e.g. ₹500.00).

    Examples:
        format_inr(50000)                     -> '₹500'
        format_inr(50050)                     -> '₹500.50'
        format_inr(50000, always_decimals=True) -> '₹500.00'
        format_inr(123456789)                 -> '₹12,34,567.89'
        format_inr(-50000)                    -> '-₹500'
        format_inr(50000, with_symbol=False)  -> '500'
    """
    _ensure_int_paise(paise)

    negative = paise < 0
    paise = abs(paise)

    rupees, fraction = divmod(paise, 100)
    rupees_str = _indian_group(rupees)

    if always_decimals:
        amount = f"{rupees_str}.{fraction:02d}"
    elif fraction:
        amount = f"{rupees_str}.{fraction:02d}"
    else:
        amount = rupees_str

    sign = "-" if negative else ""
    prefix = "₹" if with_symbol else ""
    return f"{sign}{prefix}{amount}"


def _indian_group(n: int) -> str:
    """
    Group digits in Indian format:
        1000     -> '1,000'
        100000   -> '1,00,000'
        1234567  -> '12,34,567'
    """
    s = str(n)
    if len(s) <= 3:
        return s

    last3 = s[-3:]
    rest = s[:-3]
    parts = []
    while len(rest) > 2:
        parts.insert(0, rest[-2:])
        rest = rest[:-2]
    if rest:
        parts.insert(0, rest)
    return ",".join(parts) + "," + last3


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_amount_paise(paise: int) -> tuple[bool, str]:
    """
    Validate an amount in paise. Returns (ok, reason).

    Rejects:
      - Non-int (including bool, float, str)
      - Negative values
      - Zero
      - Values above MAX_PAISE
    """
    # Explicit bool rejection — bool is subclass of int!
    if isinstance(paise, bool):
        return False, "Amount must be an integer, not a boolean."

    if not isinstance(paise, int):
        return False, f"Amount must be an integer number of paise (got {type(paise).__name__})."

    if paise < 0:
        return False, "Amount cannot be negative."

    if paise == 0:
        return False, "Amount cannot be zero."

    if paise > MAX_PAISE:
        return False, f"Amount exceeds max (₹{config.MAX_AMOUNT_RUPEES})."

    return True, ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _ensure_int_paise(paise: int) -> None:
    """Raise TypeError if paise is not a strict integer (rejects bool)."""
    if isinstance(paise, bool) or not isinstance(paise, int):
        raise TypeError(
            f"Amount must be an integer paise value, got {type(paise).__name__}: {paise!r}"
        )
