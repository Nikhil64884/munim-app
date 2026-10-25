"""
Unit tests for Business Engine (engine.py).
"""

from unittest.mock import patch

from app.database import add_transaction, get_balance_paise, get_or_create_customer
from app.engine import handle_message


def test_empty_message():
    res = handle_message("test_owner", "")
    assert "Namaste" in res["reply"]


@patch("app.engine.process_text_input")
def test_handle_query_balance(mock_llm):
    owner = "owner_test_query"
    cid = get_or_create_customer(owner, "Ramu")
    add_transaction(owner, cid, "Ramu", 50000, "Milk", "Debit", "Ramu 500")

    mock_llm.return_value = {
        "customer_name": "Ramu",
        "amount": 0,
        "item_desc": "",
        "transaction_type": "Query",
        "reply_message": "",
    }

    res = handle_message(owner, "Ramu ka hisaab?")
    assert "₹500" in res["reply"]


@patch("app.engine.process_text_input")
def test_handle_direct_transaction(mock_llm):
    owner = "owner_test_tx"
    mock_llm.return_value = {
        "customer_name": "Sita",
        "amount": 300,
        "item_desc": "Jama",
        "transaction_type": "Credit",
        "reply_message": "",
    }

    res = handle_message(owner, "Sita 300 jama")
    assert "₹300" in res["reply"]
    cid = get_or_create_customer(owner, "Sita")
    assert get_balance_paise(owner, cid) == -30000


@patch("app.engine.process_text_input")
def test_clarification_amount_then_name(mock_llm):
    owner = "owner_clarification_1"

    # Turn 1: "500 KAM KR DO" (amount given, name missing)
    mock_llm.return_value = {
        "customer_name": "",
        "amount": 500,
        "item_desc": "Kam",
        "transaction_type": "Clarification",
        "reply_message": "Ji, kis grahak ke naam par likhna hai?",
    }
    res1 = handle_message(owner, "500 KAM KR DO")
    assert "kis grahak" in res1["reply"].lower()

    # Turn 2: "RAMU" (provides the name)
    mock_llm.return_value = {
        "customer_name": "RAMU",
        "amount": 0,
        "item_desc": "",
        "transaction_type": "General",
        "reply_message": "",
    }
    res2 = handle_message(owner, "RAMU")
    assert "₹500" in res2["reply"]
    assert "RAMU" in res2["reply"]
    cid = get_or_create_customer(owner, "RAMU")
    assert get_balance_paise(owner, cid) == -50000


@patch("app.engine.process_text_input")
def test_clarification_name_then_amount(mock_llm):
    owner = "owner_clarification_2"

    # Turn 1: "Ramu ka likh lo" (name given, amount missing)
    mock_llm.return_value = {
        "customer_name": "Ramu",
        "amount": 0,
        "item_desc": "",
        "transaction_type": "Clarification",
        "reply_message": "Ji, Ramu ke kitne rupaye likhne hain?",
    }
    res1 = handle_message(owner, "Ramu ka likh lo")
    assert "rupaye" in res1["reply"].lower()

    # Turn 2: "500" (provides the amount)
    mock_llm.return_value = {
        "customer_name": "",
        "amount": 500,
        "item_desc": "",
        "transaction_type": "Clarification",
        "reply_message": "",
    }
    res2 = handle_message(owner, "500")
    assert "₹500" in res2["reply"]
    assert "Ramu" in res2["reply"]
    cid = get_or_create_customer(owner, "Ramu")
    assert get_balance_paise(owner, cid) == 50000


@patch("app.engine.process_text_input")
def test_ramu_500_kam_direct(mock_llm):
    owner = "owner_test_ramu_kam"
    mock_llm.return_value = {
        "customer_name": "RAMU",
        "amount": 500,
        "item_desc": "Kam",
        "transaction_type": "Credit",
        "reply_message": "",
    }

    res = handle_message(owner, "RAMU 500 KAM")
    assert "₹500" in res["reply"]
    assert "500,000" not in res["reply"]
    assert "5,00,000" not in res["reply"]
    cid = get_or_create_customer(owner, "RAMU")
    assert get_balance_paise(owner, cid) == -50000


@patch("app.engine.process_text_input")
def test_reset_account_flow(mock_llm):
    owner = "owner_test_reset"
    cid = get_or_create_customer(owner, "Ramu")
    add_transaction(owner, cid, "Ramu", 50000, "Milk", "Debit", "Ramu 500")

    # User asks to clear history
    mock_llm.return_value = {
        "customer_name": "",
        "amount": 0,
        "item_desc": "clear history",
        "transaction_type": "Reset",
        "reply_message": "",
    }
    res1 = handle_message(owner, "MERE ACCOUNT KI HISTORY CLEAR KR DO")
    assert "CLEAR/RESET" in res1["reply"]

    # User confirms with HAAN
    res2 = handle_message(owner, "HAAN")
    assert "clear kar di gayi hai" in res2["reply"]
    assert get_balance_paise(owner, cid) == 0
