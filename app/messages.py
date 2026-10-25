"""
User-facing messages (i18n).
Default: Hindi/Hinglish.
"""

import logging

LANG = "hi"

_log = logging.getLogger("MunimJi.messages")

_HI = {
    "GREETING": "Namaste! 🙏 Kripya hisaab likhwane ke liye message karein.",
    "FALLBACK": "Maaf kijiye, main samajh nahi paaya. Kripya dobaara bolein.",
    "VOICE_ERROR": "Awaaz saaf nahi aayi. Kripya likh kar bhejein.",
    "TECHNICAL_ERROR": "Kuch technical dikkat aa gayi hai.",
    "EXPORT_FAIL": "Maaf kijiye, file banane mein dikkat aayi.",
    "DEFAULT_ERROR": "Kuch toh gadbad hai...",
    "UNAUTHORIZED": "Yeh request verify nahi ho paayi.",
    "RATE_LIMITED": "Aap bahut tez message bhej rahe hain. Thoda rukein. ⏳",
    "BALANCE_OWED": "Ji, {name} par abhi {amount} ka udhaar baaki hai.",
    "BALANCE_ADVANCE": "Ji, {name} ke {amount} jama hain (Advance).",
    "BALANCE_NIL": "Ji, {name} ka hisaab nil (0) hai.",
    "CUSTOMER_NOT_FOUND": "Ji, '{name}' naam ka koi grahak nahi mila. Pehle koi entry likhein.",
    "EXPORT_SUCCESS": "Ji, aapka poora hisaab taiyaar hai. 📄",
    "CLARIFICATION_DEFAULT": "Kitne rupaye?",
    "CLARIFICATION_NAME": "Kis customer ka hisaab likhna hai?",
    "CLARIFICATION_NAME_REQUIRED": "Ji, kis grahak ke naam par likhna hai?",
    "TRANSACTION_SAVED_UDHAAR": "Ji, {name} ke naam {amount} ka UDHAAR likh liya. ✅\nAb total baaki: {balance}",
    "TRANSACTION_SAVED_JAMA":   "Ji, {name} ne {amount} JAMA kiye. ✅\nAb total baaki: {balance}",
    "TRANSACTION_SAVED_SALE":   "Ji, {name} ko {amount} ka udhaar par maal becha. ✅\nAb total baaki: {balance}",
    "TRANSACTION_SAVED_CASH":   "Ji, {amount} ki nakad bikri note kar li. ✅",
    "TRANSACTION_SAVED":        "Note kar liya! ✅",
    "CONFIRM_AMOUNT_UDHAAR": (
        "⚠️ Bada amount: {name} ko {amount} ka UDHAAR likhna hai?\n"
        "Confirm ke liye *HAAN*, cancel ke liye *NAHI*."
    ),
    "CONFIRM_AMOUNT_JAMA": (
        "⚠️ Bada amount: {name} ne {amount} JAMA kiye?\n"
        "Confirm ke liye *HAAN*, cancel ke liye *NAHI*."
    ),
    "CONFIRM_AMOUNT_SALE": (
        "⚠️ Bada amount: {name} ko {amount} ka maal udhaar par diya?\n"
        "Confirm ke liye *HAAN*, cancel ke liye *NAHI*."
    ),
    "CONFIRM_AMOUNT_CASH": (
        "⚠️ Bada amount: ₹{amount} ki nakad bikri note karni hai?\n"
        "Confirm ke liye *HAAN*, cancel ke liye *NAHI*."
    ),
    "CONFIRM_CANCELLED": "Thik hai, cancel kar diya.",
    "NAME_AMBIGUOUS": (
        "Ji, do grahak milte-julte hain:\n{options}\n"
        "Sahi naam ka number bhejein, ya *NAYA* likhkar naya grahak banayein."
    ),
    "NAME_NEW_CREATED": "Ji, naya grahak '{name}' add kar diya. ✅",
    "UNDO_SUCCESS": (
        "Ji, pichli entry hata di:\n"
        "• Naam: {name}\n"
        "• Amount: {amount}\n"
        "• Type: {direction}"
    ),
    "UNDO_NOTHING": "Ji, hatane ke liye koi pichli entry nahi mili.",
    "UNDO_TOO_OLD": "Ji, ye entry bahut purani hai.",
    "SUMMARY_HEADER": "📊 Sabka hisaab:\n",
    "SUMMARY_LINE": "• {name}: {balance}\n",
    "SUMMARY_MORE": "\n… aur {count} grahak. Poori list app mein dekhein.",
    "SUMMARY_TOTAL": "\n💰 Kul udhaar baaki: {total}",
    "SUMMARY_EMPTY": "Ji, abhi kisi ka bhi udhaar baaki nahi hai.",
    "DATE_SUMMARY_HEADER": "📅 {period} ka hisaab:\n",
    "DATE_SUMMARY_LINE": "• {label}: {amount}\n",
    "DATE_SUMMARY_TOTAL": "\nTotal: {amount}",
    "CUSTOMER_LIST_EMPTY": "Abhi koi grahak nahi hai.",
    "RESET_CONFIRM": (
        "⚠️ Kya aap sach mein apna poora hisaab aur customer history CLEAR/RESET karna chahte hain?\n"
        "Confirm ke liye *HAAN*, cancel ke liye *NAHI*."
    ),
    "RESET_SUCCESS": "Ji, aapke account ka saara hisaab aur history clear kar di gayi hai. ✅",
}

_EN = {k: v for k, v in _HI.items()}  # English fallback — same keys

_REGISTRY = {"hi": _HI, "en": _EN}


def msg(key: str, **kwargs) -> str:
    table = _REGISTRY.get(LANG, _HI)
    if key not in table:
        _log.warning(f"Missing message key: {key}")
        return key
    tpl = table[key]
    return tpl.format(**kwargs) if kwargs else tpl
