"""
Munim Ji — Payment Reminder Service
Handles generation of UPI payment reminders for outstanding dues.
"""

import urllib.parse

from app.config import config
from app.database import list_customers_with_balance


def get_outstanding_customers(owner_id: str) -> list[dict]:
    """Return customers whose current balance is positive."""
    rows = list_customers_with_balance(owner_id)
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "balance_paise": row["balance_paise"],
            "balance_rupees": row["balance_paise"] / 100.0,
        }
        for row in rows
        if row["balance_paise"] > 0
    ]

def generate_upi_link(
    owner_id: str,
    customer_name: str,
    amount_rupees: float,
    note: str = "Munim Ji Udhaar Bhugtan",
) -> str | None:
    """Generate a payment link only when a real merchant UPI ID is configured."""
    if not config.MERCHANT_UPI_ID:
        return None

    params = {
        "pa": config.MERCHANT_UPI_ID,
        "pn": config.MERCHANT_NAME,
        "am": f"{amount_rupees:.2f}",
        "tn": note,
        "cu": "INR",
    }

    query_string = urllib.parse.urlencode(params)
    return f"upi://pay?{query_string}"

def format_reminder_message(customers, owner_id: str = ""):
    """Format the reminder list for shopkeeper review"""
    if not customers:
        return "Sabhi customers ka udhaar clear hai! 🎉"

    lines = ["📋 *Udhaar Yaad Delai List*:\n"]
    total = 0

    for i, cust in enumerate(customers, 1):
        balance = cust['balance_rupees']
        total += balance
        upi_link = generate_upi_link(
            owner_id=owner_id,
            customer_name=cust['name'],
            amount_rupees=balance
        )
        lines.append(f"{i}. {cust['name']}: ₹{balance:.2f}")
        if upi_link:
            lines.append(f"   UPI: {upi_link}")

    lines.append(f"\n💰 *Total Udhaar*: ₹{total:.2f}")
    lines.append("\nReply with 'haan bhejo' to send reminders via your app")
    return "\n".join(lines)

def process_reminder_approval(owner_id, customer_selections=None):
    """
    Process approved reminders - returns data for mobile app to handle sending.
    customer_selections: None (all) or list of customer IDs to remind
    """
    customers = get_outstanding_customers(owner_id)

    if customer_selections is not None:
        customers = [c for c in customers if c['id'] in customer_selections]

    if not customers:
        return {"status": "no_dues", "message": "No outstanding dues to remind"}

    # Prepare data for mobile app to handle actual sending
    reminders = []
    total_amount = 0

    for cust in customers:
        upi_link = generate_upi_link(
            owner_id=owner_id,
            customer_name=cust['name'],
            amount_rupees=cust['balance_rupees']
        )

        message = (
            f"Namaste {cust['name']}, aapka udhaar ₹{cust['balance_rupees']:.2f} hai."
        )
        if upi_link:
            message += f" Kripya UPI link se bhugtan karein: {upi_link}"
        else:
            message += " Bhugtan ke liye dukaan se sampark karein."

        reminders.append({
            "customer_id": cust['id'],
            "customer_name": cust['name'],
            "amount_rupees": cust['balance_rupees'],
            "upi_link": upi_link,
            "message": message,
        })
        total_amount += cust['balance_rupees']

    return {
        "status": "ready_for_send",
        "total_customers": len(reminders),
        "total_amount_rupees": total_amount,
        "reminders": reminders,
        "note": "Mobile app should handle actual sending via WhatsApp/SMS/etc."
    }
