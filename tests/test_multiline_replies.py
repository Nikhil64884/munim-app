import json
from unittest.mock import patch

from app.engine import _handle_reminder_approval
from app.services.inventory import get_low_stock_alert


def test_multiline_replies_use_real_newlines():
    with patch(
        "app.services.inventory.get_low_stock_items",
        return_value=[
            {
                "item_name": "Doodh",
                "current_stock": 2,
                "low_stock_threshold": 5,
                "unit": "packet",
            }
        ],
    ):
        inventory_reply = get_low_stock_alert("owner-multiline")["message"]

    reminder_result = {
        "status": "ready_for_send",
        "total_customers": 1,
        "total_amount_rupees": 500.0,
        "reminders": [{"customer_name": "Ramu", "amount_rupees": 500.0}],
    }
    with patch("app.engine.process_reminder_approval", return_value=reminder_result):
        reminder_reply = _handle_reminder_approval(
            "owner-multiline",
            "haan",
            {"pending_payload": json.dumps({"reminder_text": "review"})},
        )["reply"]

    for reply in (inventory_reply, reminder_reply):
        assert "\n" in reply
        assert "\\n" not in reply
