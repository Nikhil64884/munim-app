"""Regression tests for the bugs fixed in the 2026-10 audit.

Each test maps to a finding in BUG_REPORT.md; the name says which behaviour
must never regress.
"""

import json
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.brain import _smart_rule_fallback
from app.config import _Config
from app.database import (
    AuditLog,
    Customer,
    Transaction,
    add_transaction,
    get_db,
    get_or_create_customer,
    mark_message_processed,
    peek_context,
    save_context,
)
from app.engine import handle_message
from app.messages import msg
from app.services.amounts import extract_amount_rupees

PHONE = "+919876543210"


# ---------------------------------------------------------------------------
# P0-1 / P0-5 — configuration must boot in production and refuse dev OTP
# ---------------------------------------------------------------------------
_PROD_ENV = {
    "ENV": "production",
    "OTP_PROVIDER": "twilio",
    "TWILIO_ACCOUNT_SID": "sid",
    "TWILIO_AUTH_TOKEN": "token",
    "TWILIO_VERIFY_SERVICE_SID": "vsid",
    "CORS_ORIGINS": "https://app.example.com",
    "DEV_OTP": "987654",
    "JWT_SECRET": "a" * 40,
    "GROQ_API_KEY": "test-key",
}


def _apply_env(monkeypatch, env: dict) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def test_production_config_boots_when_it_only_needs_to_warn(monkeypatch):
    """P0-1: the warning branch used an undefined `logger` and crashed on import."""
    _apply_env(monkeypatch, {**_PROD_ENV, "CORS_ORIGINS": "*"})
    config = _Config()
    assert config.IS_PROD is True
    assert config.CORS_ALLOW_ALL is True


def test_production_refuses_dev_otp_provider(monkeypatch):
    """P0-5: OTP_PROVIDER=dev in production is a full authentication bypass."""
    _apply_env(monkeypatch, {**_PROD_ENV, "OTP_PROVIDER": "dev"})
    with pytest.raises(ValueError, match="OTP_PROVIDER=dev"):
        _Config()


def test_dev_config_still_allows_dev_otp(monkeypatch):
    _apply_env(
        monkeypatch,
        {**_PROD_ENV, "ENV": "dev", "OTP_PROVIDER": "dev", "CORS_ORIGINS": "*"},
    )
    config = _Config()
    assert config.IS_PROD is False
    assert config.OTP_PROVIDER == "dev"


# ---------------------------------------------------------------------------
# P0-2 — "reset my account" must actually reset, and audit it
# ---------------------------------------------------------------------------
def test_reset_flow_soft_deletes_transactions_and_audits():
    owner = "regression-reset"
    customer_id = get_or_create_customer(owner, "Ramu")
    add_transaction(owner, customer_id, "Ramu", 50000, "Milk", "Debit", "Ramu 500")

    reset = {
        "customer_name": "",
        "amount": 0,
        "item_desc": "reset",
        "transaction_type": "Reset",
        "reply_message": "",
    }
    with patch("app.engine.process_text_input", return_value=reset):
        handle_message(owner, "poora khata reset karo")
        reply = handle_message(owner, "haan")

    assert "clear kar di gayi hai" in reply["reply"]
    with get_db() as db:
        assert db.query(Customer).filter_by(owner_id=owner).count() == 0
        transaction = db.query(Transaction).filter_by(owner_id=owner).one()
        assert transaction.is_deleted == 1
        assert transaction.customer_id is None
        assert (
            db.query(AuditLog)
            .filter_by(owner_id=owner, action="RESET_ALL_DATA")
            .count()
            == 1
        )


# ---------------------------------------------------------------------------
# P0-3 / P0-4 — account deletion works and scrubs PII
# ---------------------------------------------------------------------------
def test_account_deletion_anonymises_pii(client, auth_headers):
    customer_id = get_or_create_customer(PHONE, "Ramu Sharma")
    add_transaction(
        PHONE, customer_id, "Ramu Sharma", 50000, "Milk", "Debit",
        "Ramu Sharma ko 500 udhaar",
    )

    response = client.delete("/api/account", headers=auth_headers)
    assert response.status_code == 200

    with get_db() as db:
        transaction = db.query(Transaction).filter_by(owner_id=PHONE).one()
        assert transaction.customer_name == "[ANONYMIZED]"
        assert transaction.item_desc == "[ANONYMIZED]"
        assert transaction.original_message is None
        assert transaction.customer_id is None


# ---------------------------------------------------------------------------
# P1-2 — the reminder prompt's own suggested reply must work
# ---------------------------------------------------------------------------
def test_reminder_approval_accepts_haan_bhejo():
    owner = "regression-reminder"
    payload = json.dumps({"customer_count": 1, "reminder_text": "LIST"})
    save_context(owner, None, "ReminderRequest", "reminder_approval", payload)

    result = {
        "status": "ready_for_send",
        "total_customers": 1,
        "total_amount_rupees": 500.0,
        "reminders": [{"customer_name": "Ramu", "amount_rupees": 500.0}],
    }
    with patch("app.engine.process_reminder_approval", return_value=result):
        reply = handle_message(owner, "haan bhejo")

    assert "reminder tayyar" in reply["reply"]
    assert peek_context(owner) is None


def test_affirmative_and_negative_phrases_are_understood():
    from app.services.transactions import is_affirmative, is_negative

    for text in ("haan", "haan bhejo", "theek hai", "OK!", "👍", "1"):
        assert is_affirmative(text), text
    for text in ("nahi", "nahi bhejo", "cancel", "nako", "❌", "0"):
        assert is_negative(text), text
    # A negative must never be read as an affirmative.
    assert not is_affirmative("nahi bhejo")


# ---------------------------------------------------------------------------
# P1-3 — a pending confirmation must not trap the conversation
# ---------------------------------------------------------------------------
def _pending_confirmation(owner: str) -> None:
    save_context(
        owner,
        "Ramu",
        "Debit",
        "amount_confirmation",
        json.dumps(
            {
                "amount_paise": 600000,
                "customer_id": None,
                "customer_name": "Ramu",
                "item_desc": "Udhar",
                "transaction_type": "Debit",
                "original_message": "Ramu 6000 udhaar",
            }
        ),
    )


def test_new_transaction_cancels_pending_confirmation():
    owner = "regression-escape-txn"
    _pending_confirmation(owner)

    new_transaction = {
        "customer_name": "Sita",
        "amount": 200,
        "item_desc": "Jama",
        "transaction_type": "Credit",
        "reply_message": "",
    }
    with patch("app.engine.process_text_input", return_value=new_transaction):
        reply = handle_message(owner, "Sita 200 jama")

    assert "Sita" in reply["reply"]
    assert peek_context(owner) is None
    with get_db() as db:
        rows = db.query(Transaction).filter_by(owner_id=owner).all()
        assert len(rows) == 1
        assert rows[0].customer_name == "Sita"
        assert rows[0].transaction_type == "Credit"


def test_query_is_answered_while_a_confirmation_is_pending():
    owner = "regression-escape-query"
    customer_id = get_or_create_customer(owner, "Ramu")
    add_transaction(owner, customer_id, "Ramu", 50000, "Milk", "Debit", "Ramu 500")
    _pending_confirmation(owner)

    query = {
        "customer_name": "Ramu",
        "amount": 0,
        "item_desc": "",
        "transaction_type": "Query",
        "reply_message": "",
    }
    with patch("app.engine.process_text_input", return_value=query):
        reply = handle_message(owner, "Ramu ka hisaab?")

    assert "₹500" in reply["reply"]
    assert peek_context(owner) is None


def test_confirmation_still_saves_on_haan():
    owner = "regression-confirm-ok"
    _pending_confirmation(owner)

    reply = handle_message(owner, "haan")
    assert "₹6,000" in reply["reply"]
    with get_db() as db:
        rows = db.query(Transaction).filter_by(owner_id=owner).all()
        assert len(rows) == 1
        assert rows[0].amount_paise == 600000


# ---------------------------------------------------------------------------
# P1-4 — a new intent during a clarification must not overwrite the old one
# ---------------------------------------------------------------------------
def test_new_transaction_during_clarification_does_not_overwrite():
    owner = "regression-clarification"
    save_context(
        owner,
        "Ramu",
        "Debit",
        "clarification",
        json.dumps(
            {
                "item_desc": "Udhar",
                "original_text": "Ramu 500 udhaar",
                "amount": 500.0,
                "amount_paise": 50000,
                "transaction_type": "Debit",
            }
        ),
    )

    new_transaction = {
        "customer_name": "Sita",
        "amount": 200,
        "item_desc": "Jama",
        "transaction_type": "Credit",
        "reply_message": "",
    }
    with patch("app.engine.process_text_input", return_value=new_transaction):
        handle_message(owner, "Sita 200 jama")

    with get_db() as db:
        rows = db.query(Transaction).filter_by(owner_id=owner).all()
        assert [(r.customer_name, r.transaction_type, r.amount_paise) for r in rows] == [
            ("Sita", "Credit", 20000)
        ]


# ---------------------------------------------------------------------------
# P1-5 / P1-6 — the LLM-down fallback parser
# ---------------------------------------------------------------------------
def test_fallback_keeps_transaction_when_a_date_word_is_present():
    data = _smart_rule_fallback("NARESH 500 UDHAR AAJ")
    assert data is not None
    assert data["transaction_type"] == "Debit"
    assert data["amount"] == 500.0
    assert data["customer_name"] == "Naresh"


def test_fallback_does_not_leak_time_or_verb_words_into_names():
    assert _smart_rule_fallback("Ramu ko kal 500 udhaar")["customer_name"] == "Ramu"
    assert _smart_rule_fallback("Ramu ne 500 jama kiye")["customer_name"] == "Ramu"


def test_fallback_still_recognises_summaries():
    assert _smart_rule_fallback("aaj ka hisaab")["transaction_type"] == "DateSummary"
    assert _smart_rule_fallback("sabka hisaab")["transaction_type"] == "AllCustomers"


# ---------------------------------------------------------------------------
# P2-2 — "saath" means "with", not 60
# ---------------------------------------------------------------------------
def test_saath_is_not_parsed_as_sixty():
    assert extract_amount_rupees("Ramu ke saath paanch sau") == Decimal("500")
    assert extract_amount_rupees("paanch sau") == Decimal("500")


# ---------------------------------------------------------------------------
# P2-1 — big cash sales get the cash confirmation wording
# ---------------------------------------------------------------------------
def test_cash_sale_confirmation_wording():
    owner = "regression-cash"
    cash_sale = {
        "customer_name": "",
        "amount": 6000,
        "item_desc": "Cash Sale",
        "transaction_type": "CashSale",
        "reply_message": "",
    }
    with patch("app.engine.process_text_input", return_value=cash_sale):
        prompt = handle_message(owner, "6000 nakad bikri")
        confirmed = handle_message(owner, "haan")

    assert "nakad bikri" in prompt["reply"]
    assert "JAMA" not in prompt["reply"]
    assert "? ne" not in prompt["reply"]
    assert "nakad bikri" in confirmed["reply"]
    with get_db() as db:
        row = db.query(Transaction).filter_by(owner_id=owner).one()
        assert row.transaction_type == "CashSale"
        assert row.amount_paise == 600000


# ---------------------------------------------------------------------------
# P2-3 / P2-4 — query and summary edge cases
# ---------------------------------------------------------------------------
def test_query_for_unknown_customer_says_not_found():
    owner = "regression-unknown"
    query = {
        "customer_name": "Nobody",
        "amount": 0,
        "item_desc": "",
        "transaction_type": "Query",
        "reply_message": "",
    }
    with patch("app.engine.process_text_input", return_value=query):
        reply = handle_message(owner, "Nobody ka hisaab?")

    assert msg("CUSTOMER_NOT_FOUND", name="Nobody") == reply["reply"]


def test_all_customers_mentions_hidden_entries():
    owner = "regression-many"
    for index in range(25):
        customer_id = get_or_create_customer(owner, f"Cust{index:02d}")
        add_transaction(owner, customer_id, f"Cust{index:02d}", 10000, "x", "Debit", "x")

    summary = {
        "customer_name": "",
        "amount": 0,
        "item_desc": "",
        "transaction_type": "AllCustomers",
        "reply_message": "",
    }
    with patch("app.engine.process_text_input", return_value=summary):
        reply = handle_message(owner, "sabka hisaab")

    assert "aur 5 grahak" in reply["reply"]


# ---------------------------------------------------------------------------
# P2-6 — customer phone numbers are persisted, not silently dropped
# ---------------------------------------------------------------------------
def test_customer_phone_is_persisted(client, auth_headers):
    response = client.post(
        "/api/customers",
        json={"name": "Ramu", "phone": "+919812345678"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    with get_db() as db:
        customer = db.query(Customer).filter_by(name_lower="ramu").one()
        assert customer.phone == "+919812345678"


# ---------------------------------------------------------------------------
# P2-7 — a failed voice request must not block retries
# ---------------------------------------------------------------------------
def test_voice_failure_clears_idempotency_marker(client, auth_headers, monkeypatch):
    from app.routes import chat

    async def fake_transcribe(_file):
        return "Ramu 500 udhaar", None

    def boom(_owner_id, _text):
        raise RuntimeError("simulated voice processing failure")

    monkeypatch.setattr(chat, "transcribe_upload", fake_transcribe)
    monkeypatch.setattr(chat, "handle_message", boom)

    with pytest.raises(RuntimeError, match="simulated voice processing failure"):
        client.post(
            "/api/voice",
            headers=auth_headers,
            files={"file": ("clip.mp3", b"fake-audio", "audio/mpeg")},
            data={"client_msg_id": "voice-retry"},
        )

    assert mark_message_processed(f"app:{PHONE}:voice-retry") is True
