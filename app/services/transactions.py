"""Transaction helpers — direction words, affirmatives, reply composition."""

import logging
import re

from app.database import get_balance_paise
from app.messages import msg
from app.money import format_inr

logger = logging.getLogger("MunimJi.services.transactions")

INCREASE_OWED = {"Debit", "Sale"}
DECREASE_OWED = {"Credit"}
NEUTRAL = {"CashSale"}

# Exact answers, and the first word of longer ones ("haan bhejo", "theek hai").
_AFFIRMATIVES = {
    "haan", "han", "ha", "yes", "y", "confirm", "confirmed",
    "theek", "thik", "ok", "okay", "sahi", "1", "👍", "✅",
}
_NEGATIVES = {
    "nahi", "nahin", "no", "n", "cancel", "galat", "mat", "nako", "0", "❌",
}


def _normalize(text: str) -> str:
    """Lowercase and strip punctuation/emoji so 'HAAN!' still matches."""
    cleaned = re.sub(r"[^\w\s]", " ", (text or "").lower())
    return " ".join(cleaned.split())


def _matches(text: str, vocabulary: set[str]) -> bool:
    raw = (text or "").strip().lower()
    if raw in vocabulary:  # emoji-only replies survive normalization
        return True
    normalized = _normalize(text)
    if not normalized:
        return False
    if normalized in vocabulary:
        return True
    # Allow a short reply that continues with more words: "haan bhejo".
    first = normalized.split(" ", 1)[0]
    return first in vocabulary


def direction_word(tx_type: str) -> str:
    if tx_type in INCREASE_OWED:
        return "UDHAAR"
    if tx_type in DECREASE_OWED:
        return "JAMA"
    if tx_type in NEUTRAL:
        return "NAKAD"
    return tx_type.upper()


def is_affirmative(text: str) -> bool:
    return _matches(text, _AFFIRMATIVES)


def is_negative(text: str) -> bool:
    return _matches(text, _NEGATIVES)


def compose_saved_reply(
    tx_type: str,
    customer_id: int | None,
    name: str,
    amount_paise: int,
    owner_id: str,
) -> str:
    """Server-composed reply — never trust LLM wording for confirmed transactions."""
    amount_str = format_inr(amount_paise)

    if tx_type == "CashSale":
        return msg("TRANSACTION_SAVED_CASH", amount=amount_str)

    if customer_id:
        balance = get_balance_paise(owner_id, customer_id)
    else:
        balance = amount_paise if tx_type in INCREASE_OWED else -amount_paise

    if balance > 0:
        balance_str = f"{format_inr(balance)} (Udhaar)"
    elif balance < 0:
        balance_str = f"{format_inr(abs(balance))} (Advance)"
    else:
        balance_str = "Nil (₹0)"

    if tx_type in INCREASE_OWED:
        key = "TRANSACTION_SAVED_SALE" if tx_type == "Sale" else "TRANSACTION_SAVED_UDHAAR"
    elif tx_type in DECREASE_OWED:
        key = "TRANSACTION_SAVED_JAMA"
    else:
        return msg("TRANSACTION_SAVED")

    return msg(key, name=name, amount=amount_str, balance=balance_str)
