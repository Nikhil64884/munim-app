from unittest.mock import patch

from app.database import Transaction, get_db, peek_context
from app.engine import handle_message


def test_duplicate_confirmation_creates_only_one_transaction():
    owner = "owner-duplicate-confirmation"
    transaction_data = {
        "customer_name": "Ramu",
        "amount": 6000,
        "item_desc": "Udhar",
        "transaction_type": "Debit",
        "reply_message": "",
    }

    with patch("app.engine.process_text_input", return_value=transaction_data):
        handle_message(owner, "Ramu 6000 udhaar")

    pending = peek_context(owner)
    assert pending is not None

    # Simulate the same confirmation being delivered twice with the same context.
    with patch("app.engine.peek_context", return_value=pending):
        handle_message(owner, "haan")
        handle_message(owner, "haan")

    with get_db() as db:
        count = db.query(Transaction).filter_by(owner_id=owner).count()
    assert count == 1
