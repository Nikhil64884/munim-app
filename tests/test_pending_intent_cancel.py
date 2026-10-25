from unittest.mock import patch

from app.database import Transaction, get_db
from app.engine import handle_message


def test_reset_during_pending_clarification_does_not_create_transaction():
    owner = "owner-reset-during-clarification"
    clarification = {
        "customer_name": "",
        "amount": 500,
        "item_desc": "Udhar",
        "transaction_type": "Clarification",
        "reply_message": "Ji, kis naam par likhna hai?",
    }
    reset = {
        "customer_name": "",
        "amount": 0,
        "item_desc": "reset",
        "transaction_type": "Reset",
        "reply_message": "",
    }

    with patch("app.engine.process_text_input", side_effect=[clarification, reset]):
        handle_message(owner, "₹500 kis naam par?")
        result = handle_message(owner, "reset")

    with get_db() as db:
        count = db.query(Transaction).filter_by(owner_id=owner).count()
    assert count == 0
    assert "CLEAR/RESET" in result["reply"]


def test_pending_name_step_rejects_random_short_text_but_accepts_name():
    owner = "owner-pending-name-validation"
    clarification = {
        "customer_name": "",
        "amount": 500,
        "item_desc": "Udhar",
        "transaction_type": "Clarification",
        "reply_message": "Ji, kis naam par likhna hai?",
    }
    random_text = {
        "customer_name": "",
        "amount": 0,
        "item_desc": "",
        "transaction_type": "General",
        "reply_message": "",
    }
    extracted_name = {
        "customer_name": "Ramesh",
        "amount": 0,
        "item_desc": "",
        "transaction_type": "General",
        "reply_message": "",
    }

    with patch("app.engine.process_text_input", side_effect=[clarification, random_text]):
        handle_message(owner, "₹500 kis naam par?")
        handle_message(owner, "ok")

    with get_db() as db:
        assert db.query(Transaction).filter_by(owner_id=owner).count() == 0

    with patch("app.engine.process_text_input", return_value=extracted_name):
        result = handle_message(owner, "Ramesh")

    with get_db() as db:
        assert db.query(Transaction).filter_by(owner_id=owner).count() == 1
    assert "Ramesh" in result["reply"]
