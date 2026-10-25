"""
Munim Ji AI — Brain Module.

- Text → structured JSON via Groq LLM (Llama 3.3)
- Audio → text via Groq Whisper
- Retries with exponential backoff
- Robust JSON extraction
- Rejects NaN/Inf and negative amounts
"""

import json
import logging
import math
import re
import time

from groq import Groq

from app.config import config
from app.services.amounts import extract_amount_rupees

logger = logging.getLogger("MunimJi.brain")

client = Groq(api_key=config.GROQ_API_KEY, timeout=config.GROQ_TIMEOUT_SECONDS)

# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """
You are 'Munim Ji', an Indian shop accountant.

### ABSOLUTE RULES — Never break these

1. AMOUNT MUST COME FROM THE CURRENT MESSAGE ONLY.
   - If user says "rammu 500 jama" → amount MUST be 500
   - NEVER use a previous message's amount
   - NEVER invent an amount
   - If no number in current message → amount = 0

2. NAME MUST COME FROM THE CURRENT MESSAGE ONLY.
   - If user says "rammu 500 jama" → customer_name = "rammu"
   - NEVER substitute with a previous customer's name
   - NEVER auto-correct spelling (rammu stays rammu)
   - Extract ONLY what user typed

3. HINDI SPELLING VARIATIONS — all mean the same:
   - UDHAAR: udhaar, udhar, udhari, udar, baaki, baki, likh lo, de do, diya
   - JAMA: jama, jma, jamaa, jamma, jamah, payment, paisa diya, kam, ghatao, minus, liye, mila, aagaya
   - CASH: cash, nakad, cash sale

### REMINDER REQUESTS
If user asks for payment reminders/dues list:
- "yaad delai", "reminder bhejo", "kis ka paisa hai", "udhaar list"
- transaction_type = "ReminderRequest"
- amount = 0
- item_desc = "reminder"
- reply_message = appropriate prompt in Hindi/Hinglish

### INVENTORY REQUESTS
If user asks for inventory/stock information:
- "low stock alert", "stock alert", "low stock", "item stock", "inventory"
- transaction_type = "InventoryRequest"
- amount = 0
- item_desc = "" (or item name if specified, e.g., "low stock alert for item" -> item_desc = "item")
- reply_message = appropriate prompt in Hindi/Hinglish

### Transaction Types

If user gives transaction with name + amount:
- "jama" / "kam" / "minus" type words → transaction_type = "Credit"
- "udhaar" type words → transaction_type = "Debit"
- "cash" / "nakad" → transaction_type = "CashSale"
- specific item sale → "Sale"

If amount missing → transaction_type = "Clarification"
If name missing → transaction_type = "Clarification"

### JSON Output (ONLY this)

{
  "customer_name": str,
  "amount": number,
  "item_desc": str,
  "transaction_type": one of
    ["Debit","Sale","Credit","CashSale","Query","AllCustomers","DateSummary",
     "Clarification","General","Export","Undo","Reset","ReminderRequest","InventoryRequest"],
  "reply_message": str
}

### EXAMPLES — Memorize

User: "rammu 500 jama"
Output: {"customer_name":"rammu","amount":500,"item_desc":"Jama","transaction_type":"Credit","reply_message":""}

User: "RAMU 500 KAM"
Output: {"customer_name":"RAMU","amount":500,"item_desc":"Kam","transaction_type":"Credit","reply_message":""}

User: "500 KAM KR DO"
Output: {"customer_name":"","amount":500,"item_desc":"Kam","transaction_type":"Clarification","reply_message":"Ji, kis grahak ke naam par kam karna hai?"}

User: "Ramu ko 500 udhaar"
Output: {"customer_name":"Ramu","amount":500,"item_desc":"Udhar","transaction_type":"Debit","reply_message":""}

User: "Sita ne 200 jama kiye"
Output: {"customer_name":"Sita","amount":200,"item_desc":"Jama","transaction_type":"Credit","reply_message":""}

User: "Mohan ko 1000 udhaar"
Output: {"customer_name":"Mohan","amount":1000,"item_desc":"Udhar","transaction_type":"Debit","reply_message":""}

User: "Ramu ka likh lo"
Output: {"customer_name":"Ramu","amount":0,"item_desc":"","transaction_type":"Clarification","reply_message":"Ji, Ramu ke kitne rupaye likhne hain?"}

User: "500 likh do"
Output: {"customer_name":"","amount":500,"item_desc":"","transaction_type":"Clarification","reply_message":"Ji, kis grahak ke naam par likhna hai?"}

User: "sabka hisaab"
Output: {"customer_name":"","amount":0,"item_desc":"","transaction_type":"AllCustomers","reply_message":""}

User: "aaj ka hisaab"
Output: {"customer_name":"","amount":0,"item_desc":"today","transaction_type":"DateSummary","reply_message":""}

User: "MERE ACCOUNT KI HISTORY CLEAR KR DO"
Output: {"customer_name":"","amount":0,"item_desc":"clear history","transaction_type":"Reset","reply_message":""}

User: "poora khata reset karo"
Output: {"customer_name":"","amount":0,"item_desc":"reset","transaction_type":"Reset","reply_message":""}
"""
# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REQUIRED_KEYS = {"customer_name", "transaction_type", "reply_message", "amount"}

ALLOWED_TYPES = {
    "Debit", "Sale", "Credit", "CashSale",
    "Query", "AllCustomers", "DateSummary",
    "Clarification", "General", "Export", "Undo", "Reset", "ReminderRequest", "InventoryRequest",
}

DB_WRITABLE_TYPES = {"Debit", "Sale", "Credit", "CashSale"}


# ---------------------------------------------------------------------------
# JSON extraction (balanced-brace scan)
# ---------------------------------------------------------------------------
def _extract_json(text: str) -> dict | None:
    """Extract first valid JSON object using balanced-brace scan."""
    if not text:
        return None

    # Fast path
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Balanced brace scan
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    start = None

    logger.warning("No valid JSON in LLM output.")
    return None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _validate(data: dict | None) -> dict | None:
    if not data:
        return None

    missing = REQUIRED_KEYS - set(data.keys())
    if missing:
        logger.warning(f"LLM missing keys: {missing}")
        return None

    tx_type = str(data.get("transaction_type", "")).strip()
    norm = tx_type.capitalize() if tx_type.islower() else tx_type
    if tx_type.lower() == "cashsale":
        norm = "CashSale"
    if norm not in ALLOWED_TYPES:
        logger.warning(f"Unrecognised type: {tx_type}")
        return None
    data["transaction_type"] = norm

    raw_amount = data.get("amount")
    try:
        amount = float(raw_amount or 0)
    except (TypeError, ValueError):
        logger.warning(f"Non-numeric amount: {raw_amount!r}")
        return None

    if math.isnan(amount) or math.isinf(amount) or amount < 0:
        logger.warning(f"Invalid amount: {raw_amount!r}")
        return None

    data["amount"] = amount
    data["customer_name"] = str(data.get("customer_name") or "").strip()
    data["item_desc"] = str(data.get("item_desc") or "").strip()
    data["reply_message"] = str(data.get("reply_message") or "").strip()

    if amount == 0 and norm in DB_WRITABLE_TYPES:
        data["transaction_type"] = "Clarification"
        if not data["reply_message"]:
            data["reply_message"] = "Ji, kitne rupaye likhne hain?"

    return data


# ---------------------------------------------------------------------------
# Audio transcription
# ---------------------------------------------------------------------------
def transcribe_audio(file_path: str) -> str | None:
    """Transcribe audio via Groq Whisper."""
    try:
        with open(file_path, "rb") as f:
            completion = client.audio.transcriptions.create(
                file=(file_path, f.read()),
                model=config.WHISPER_MODEL,
                response_format="text",
            )
        logger.info("Audio transcription completed.")
        return completion
    except Exception as e:
        logger.error(f"Transcription error: {e}", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------
def process_text_input(text: str) -> dict | None:
    """Call Groq with retries. Returns validated dict or None."""
    last_err = None
    for attempt in range(config.GROQ_MAX_RETRIES + 1):
        try:
            completion = client.chat.completions.create(
                model=config.GROQ_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )
            content = completion.choices[0].message.content
            logger.info("LLM processing completed successfully.")
            return _validate(_extract_json(content))
        except Exception as e:
            last_err = e
            err_str = str(e)
            # Auth/key problems and unknown models will not fix themselves.
            if any(
                marker in err_str
                for marker in (
                    "401", "403", "404", "Access denied", "model_not_found",
                    "does not exist or you do not have access",
                )
            ):
                logger.warning(f"Groq API key/model error ({e}). Switching immediately to smart rule parser.")
                break
            if attempt < config.GROQ_MAX_RETRIES:
                sleep = 0.5 * (2 ** attempt)
                logger.warning(f"Groq attempt {attempt + 1} failed: {e}. Retry in {sleep}s.")
                time.sleep(sleep)

    logger.error(f"Groq failed after retries: {last_err}")
    return _smart_rule_fallback(text)


# ---------------------------------------------------------------------------
# Fallback parser (used only when the LLM is unreachable)
# ---------------------------------------------------------------------------
_SUMMARY_WORDS = (
    "hisaab", "hisab", "summary", "total", "report", "batao", "kitna", "kitne", "list",
)
_ALL_WORDS = ("sabka", "sab", "all", "kul", "sabhi")
_PERIOD_WORDS = {
    "yesterday": ("kal", "yesterday"),
    "week": ("hafte", "hafta", "week"),
    "month": ("mahine", "mahina", "month"),
}
_DATE_WORDS = ("aaj", "kal", "parso", "date", "din", "hafte", "mahine")

# Words that are never part of a customer name: particles, verbs, number
# words, accounting vocabulary, and time words (aaj/kal used to leak into
# names, creating ghost customers like "Ramu Kal").
_STOP_WORDS = {
    # particles / postpositions
    "ko", "ne", "par", "se", "ka", "ki", "ke", "mein", "me", "aur", "bhi", "ek",
    # currency
    "rupaye", "rupee", "rupees", "rupya", "rs", "inr",
    # accounting words
    "udhaar", "udhar", "udhari", "jama", "jma", "jamaa", "jamma", "jamaah",
    "cash", "rokad", "nagad", "nakad", "baki", "baaki", "hisaab", "hisab",
    "account", "khata", "kaam", "bikri", "sale", "payment",
    # verbs / helpers
    "likho", "likh", "likhna", "likhdo", "karo", "kar", "kardo", "karein",
    "kijiye", "dijiye", "dena", "de", "do", "diya", "diye", "liya", "liye",
    "mila", "mile", "aaya", "aaye", "aagaya", "hua", "hui", "hue", "kiya",
    "kiye", "gaya", "gaye", "gayi", "tha", "the", "thi", "hai", "hain", "ho",
    "hoga", "karwaye", "karwaya", "karwa", "bhejo", "bhej", "bhejna",
    # time words
    "aaj", "kal", "parso", "abhi", "phir", "pichle", "date", "din", "hafte",
    "hafta", "mahine", "mahina", "subah", "shaam",
}


def _fallback_period(tl: str) -> str:
    for period, words in _PERIOD_WORDS.items():
        if any(w in tl for w in words):
            return period
    return "today"


def _smart_rule_fallback(text: str) -> dict | None:
    """Fallback parser for common accounting phrases when LLM is unavailable.

    Order matters: amounts are parsed first, so a sentence like
    "Naresh 500 udhaar aaj" is still saved as a transaction instead of being
    mistaken for a day summary.
    """
    t = text.strip()
    tl = t.lower()

    amount_dec = extract_amount_rupees(t)
    amt = float(amount_dec) if amount_dec is not None else 0.0
    if amt == 0:
        digit_match = re.search(r"\b(\d+(?:\.\d{1,2})?)\b", t)
        amt = float(digit_match.group(1)) if digit_match else 0.0

    has_summary_word = any(w in tl for w in _SUMMARY_WORDS)

    # Read-only intents — only when the user gave no amount.
    if amt == 0:
        if has_summary_word and any(w in tl for w in _ALL_WORDS):
            return {
                "customer_name": "",
                "amount": 0,
                "item_desc": "",
                "transaction_type": "AllCustomers",
                "reply_message": "",
            }
        if has_summary_word and any(w in tl for w in _DATE_WORDS):
            return {
                "customer_name": "",
                "amount": 0,
                "item_desc": _fallback_period(tl),
                "transaction_type": "DateSummary",
                "reply_message": "",
            }
        if any(w in tl for w in ("inventory", "stock")):
            return {
                "customer_name": "",
                "amount": 0,
                "item_desc": "",
                "transaction_type": "InventoryRequest",
                "reply_message": "",
            }
        if any(w in tl for w in ("reminder", "yaad")):
            return {
                "customer_name": "",
                "amount": 0,
                "item_desc": "reminder",
                "transaction_type": "ReminderRequest",
                "reply_message": "",
            }

    tx_type = "Clarification"
    desc = "Hisaab"
    if any(w in tl for w in ("udhaar", "udhar", "baaki", "baki", "diye", "debit")):
        tx_type = "Debit"
        desc = "Udhaar"
    elif any(w in tl for w in ("jama", "jamaa", "jma", "mile", "credit", "aaye", "aaya", "pay")):
        tx_type = "Credit"
        desc = "Jama"
    elif any(w in tl for w in ("cash", "rokad", "nagad", "nakad")):
        tx_type = "CashSale"
        desc = "Cash Sale"

    name_words = []
    for word in t.split():
        cleaned = re.sub(r"[^a-zA-Z\u0900-\u097F]", "", word)
        if not cleaned or cleaned.lower() in _STOP_WORDS or re.match(r"^\d+$", word):
            continue
        name_words.append(cleaned.capitalize())

    name = " ".join(name_words)

    if tx_type == "CashSale" and amt > 0:
        return {
            "customer_name": "",
            "amount": amt,
            "item_desc": desc,
            "transaction_type": "CashSale",
            "reply_message": "",
        }

    if name and amt > 0:
        return {
            "customer_name": name,
            "amount": amt,
            "item_desc": desc,
            "transaction_type": tx_type,
            "reply_message": "",
        }

    if name and amt == 0:
        return {
            "customer_name": name,
            "amount": 0,
            "item_desc": desc,
            "transaction_type": "Clarification",
            "reply_message": "Ji, kitne rupaye likhne hain?",
        }

    if amt > 0:
        return {
            "customer_name": "",
            "amount": amt,
            "item_desc": desc,
            "transaction_type": "Clarification",
            "reply_message": "Ji, kis grahak ke naam par hisaab likhna hai?",
        }

    return None
