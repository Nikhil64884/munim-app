"""
Unit tests for Brain module (brain.py) parsing & validation.
"""

from app.brain import _extract_json, _validate


def test_extract_json():
    # Direct JSON
    assert _extract_json('{"key": "value"}') == {"key": "value"}

    # Extra text around JSON
    markdown_wrapped = 'Sure, here is the result: ```json\n{"customer_name": "Ramu", "amount": 500}\n```'
    extracted = _extract_json(markdown_wrapped)
    assert extracted is not None
    assert extracted.get("customer_name") == "Ramu"
    assert extracted.get("amount") == 500


def test_validate_llm_output():
    valid = {
        "customer_name": "Ramu",
        "amount": 500,
        "item_desc": "Udhar",
        "transaction_type": "Debit",
        "reply_message": "",
    }
    validated = _validate(valid)
    assert validated is not None
    assert validated["amount"] == 500.0
    assert validated["transaction_type"] == "Debit"

    # Missing required keys
    assert _validate({"customer_name": "Ramu"}) is None

    # Invalid amount (negative or non-numeric)
    invalid_amt = dict(valid)
    invalid_amt["amount"] = -50
    assert _validate(invalid_amt) is None

    # Unknown transaction type
    invalid_type = dict(valid)
    invalid_type["transaction_type"] = "UnknownType"
    assert _validate(invalid_type) is None
