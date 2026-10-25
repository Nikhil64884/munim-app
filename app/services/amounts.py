"""
Amount extraction from text (regex + Hindi word parser).

Independent of the LLM — used to cross-verify AI's amount extraction.

Handles:
    "500", "1,200", "₹750", "500 rupees"
    "5k", "2 lakh", "3 crore"
    "paanch sau"        -> 500
    "do hazaar"         -> 2000
    "dhai sau"          -> 250
    "sawa lakh"         -> 125000
    "paanch sau bees"   -> 520

Returns Decimal (never float) to avoid rounding issues in money.
"""

import logging
import re
from decimal import Decimal

logger = logging.getLogger("MunimJi.services.amounts")

# ---------------------------------------------------------------------------
# Hindi/Hinglish number words
# ---------------------------------------------------------------------------
_UNITS = {
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4,
    "paanch": 5, "panch": 5, "chhe": 6, "che": 6, "chah": 6,
    "saat": 7, "aath": 8, "nau": 9, "das": 10,
    "gyarah": 11, "barah": 12, "terah": 13, "chaudah": 14, "pandrah": 15,
    "solah": 16, "satrah": 17, "atharah": 18, "unnis": 19, "bees": 20,
    "pachees": 25, "tees": 30, "chalees": 40, "pachaas": 50, "pachas": 50,
    "sattar": 70, "assi": 80, "nabbe": 90,
    # NB: "saath" (=60) is deliberately omitted — in everyday Hindi it usually
    # means "with" ("Ramu ke saath 500"), which turned amounts into garbage.
}

# Fractions — multiplicatively applied
_FRACTIONS = {
    "dhai": Decimal("2.5"),    # 2.5 × next (dhai sau = 250)
    "sawa": Decimal("1.25"),   # 1.25 × next (sawa lakh = 125000)
    "paune": Decimal("0.75"),  # 0.75 × next (paune sau = 75)
    "dedh": Decimal("1.5"),    # 1.5 × next (dedh sau = 150)
    "adha": Decimal("0.5"),
    "aadha": Decimal("0.5"),
}

# Multipliers — scale the accumulated value
_SCALES = {
    "sau": Decimal("100"),
    "so": Decimal("100"),
    "hazaar": Decimal("1000"),
    "hazar": Decimal("1000"),
    "hajar": Decimal("1000"),
    "hajaar": Decimal("1000"),
    "lakh": Decimal("100000"),
    "lac": Decimal("100000"),
    "lakhh": Decimal("100000"),
    "crore": Decimal("10000000"),
    "karor": Decimal("10000000"),
}

# All known Hindi number tokens (for tokenizer)
_ALL_HINDI = set(_UNITS) | set(_FRACTIONS) | set(_SCALES)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def extract_amount_rupees(text: str) -> Decimal | None:
    """
    Extract amount in RUPEES as Decimal. Returns None if not found.

    Priority:
        1. Digits with optional ₹/Rs and scale suffix
        2. Hindi/Hinglish number words (compound parsing)
    """
    if not text:
        return None

    # 1) Digit-based extraction
    digit_amount = _extract_digit_amount(text)
    if digit_amount is not None:
        return digit_amount

    # 2) Hindi word-based extraction
    return _extract_hindi_amount(text)


# ---------------------------------------------------------------------------
# Digit extraction
# ---------------------------------------------------------------------------
def _extract_digit_amount(text: str) -> Decimal | None:
    """Parse '500', '1,200', '₹750', '5k', '2 lakh'."""
    matches = list(
        re.finditer(
            r"(?P<prefix>₹|rs\.?|rupees?\s*)?"
            r"(?P<amount>\d[\d,]*(?:\.\d{1,2})?)"
            r"(?:\s*(?P<suffix>k|hazaar|hazar|hajar|lakh|lac|lakhh|crore|karor)\b)?",
            text,
            re.I,
        )
    )
    if not matches:
        return None

    # 10+ digit numbers without prefix or suffix are phone numbers, not amounts.
    valid_matches = [
        m for m in matches
        if not (
            len(re.sub(r"[^\d]", "", m.group("amount"))) >= 10
            and not m.group("prefix")
            and not m.group("suffix")
        )
    ]
    if not valid_matches:
        return None
    matches = valid_matches

    # Prefer explicit currency/scale markers or a nearby transaction cue.
    # Multiple unmarked numbers are ambiguous; leave those to the language
    # model rather than guessing (e.g. "500 udhaar 2 din baad").
    marked = [m for m in matches if m.group("prefix") or m.group("suffix")]
    contextual = [
        m
        for m in matches
        if re.match(
            r"\s*(?:rupees?|rupaye|rupya|udhaar|udhar|jama|cash|diya|diye|liye|bheje|baki)\b",
            text[m.end() :],
            re.I,
        )
    ]
    candidates = marked or contextual
    if candidates:
        match = candidates[-1]
    elif len(matches) == 1:
        match = matches[0]
    else:
        return None

    try:
        base = Decimal(match.group("amount").replace(",", ""))
    except Exception:
        return None

    suffix = (match.group("suffix") or "").lower()
    mult = {
        "k": Decimal("1000"),
        "hazaar": Decimal("1000"),
        "hazar": Decimal("1000"),
        "hajar": Decimal("1000"),
        "lakh": Decimal("100000"),
        "lac": Decimal("100000"),
        "lakhh": Decimal("100000"),
        "crore": Decimal("10000000"),
        "karor": Decimal("10000000"),
    }.get(suffix)

    return base * mult if mult else base


# ---------------------------------------------------------------------------
# Hindi word extraction
# ---------------------------------------------------------------------------
def _tokenize(text: str) -> list[str]:
    """Lowercase and split into word tokens, keeping only Hindi number words."""
    tokens = re.findall(r"[a-zA-Z]+", text.lower())
    return [t for t in tokens if t in _ALL_HINDI]


def _extract_hindi_amount(text: str) -> Decimal | None:
    """
    Parse Hindi number phrases like:
        paanch sau         -> 500
        do hazaar          -> 2000
        dhai sau           -> 250
        sawa lakh          -> 125000
        paanch sau bees    -> 520
        bees hazaar pachaas-> 20050
    """
    tokens = _tokenize(text)
    if not tokens:
        return None

    # Ambiguous Hindi words like 'do' (=give) or 'so' (=then) must not be
    # parsed as amounts if they appear alone without any scale, fraction, or currency keyword.
    has_currency_keyword = bool(
        re.search(r"\b(?:rupees?|rupaye|rupya|rs\.?|inr)\b", text, re.I) or "₹" in text
    )
    has_scale_or_fraction = any(t in _SCALES or t in _FRACTIONS for t in tokens)

    # A single token from common homophones ('do', 'char', 'so', 'ek') without scale/currency is not an amount.
    if len(tokens) == 1:
        single = tokens[0]
        if single in {"do", "char", "ek", "so"} and not has_currency_keyword:
            return None

    total = Decimal("0")
    current = Decimal("0")
    has_value = False

    for tok in tokens:
        if tok in _UNITS:
            # Accumulate small number: "paanch sau" means 5 × 100
            current += Decimal(_UNITS[tok])
            has_value = True

        elif tok in _FRACTIONS:
            # Fraction multiplies the NEXT unit-scale: "dhai sau" = 2.5 × 100
            current = _FRACTIONS[tok]
            has_value = True

        elif tok in _SCALES:
            scale = _SCALES[tok]
            # current holds the coefficient; if empty, assume 1
            coefficient = current if current > 0 else Decimal("1")
            total += coefficient * scale
            current = Decimal("0")
            has_value = True

    # Remaining small number without a scale word
    if current > 0:
        total += current

    if not has_value:
        return None

    return total if total > 0 else None
