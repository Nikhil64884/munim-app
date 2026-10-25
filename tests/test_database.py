"""
Unit tests for Database layer (database.py).
"""

from app.database import (
    add_transaction,
    get_balance_by_name,
    get_balance_paise,
    get_or_create_customer,
    mark_message_processed,
    merge_customers,
    soft_delete_transaction,
    update_transaction,
)


def test_customer_creation_and_lookup():
    owner = "owner_1"
    cid1 = get_or_create_customer(owner, "Ramu")
    cid2 = get_or_create_customer(owner, "ramu")
    assert cid1 == cid2

    cid3 = get_or_create_customer(owner, "Shyam")
    assert cid3 != cid1


def test_transaction_crud_and_balance():
    owner = "owner_1"
    cid = get_or_create_customer(owner, "Ramu")

    # Debit ₹500 (50,000 paise)
    tx1_id = add_transaction(
        owner_id=owner,
        customer_id=cid,
        customer_name="Ramu",
        amount_paise=50000,
        item="Milk",
        type_="Debit",
        message="Ramu 500 udhar",
    )
    assert tx1_id > 0
    assert get_balance_paise(owner, cid) == 50000
    assert get_balance_by_name(owner, "ramu") == 50000

    # Credit ₹200 (20,000 paise)
    add_transaction(
        owner_id=owner,
        customer_id=cid,
        customer_name="Ramu",
        amount_paise=20000,
        item=None,
        type_="Credit",
        message="Ramu 200 jama",
    )
    assert get_balance_paise(owner, cid) == 30000

    # Update transaction
    update_transaction(tx1_id, owner, amount_paise=60000)
    assert get_balance_paise(owner, cid) == 40000

    # Soft delete
    soft_delete_transaction(tx1_id, owner)
    assert get_balance_paise(owner, cid) == -20000


def test_merge_customers():
    owner = "owner_2"
    c1 = get_or_create_customer(owner, "Mohan")
    c2 = get_or_create_customer(owner, "Mohan Lal")

    add_transaction(owner, c1, "Mohan", 10000, "Tea", "Debit", "Mohan 100")
    add_transaction(owner, c2, "Mohan Lal", 20000, "Sugar", "Debit", "Mohan Lal 200")

    moved = merge_customers(owner, from_id=c1, into_id=c2)
    assert moved == 1
    assert get_balance_paise(owner, c2) == 30000


def test_idempotency_marker():
    msg_sid = "test-msg-12345"
    assert mark_message_processed(msg_sid) is True
    assert mark_message_processed(msg_sid) is False


def test_input_sanitization():
    owner = "owner_sec"
    # Malicious HTML/script in customer name
    cid = get_or_create_customer(owner, "<script>alert('xss')</script>Ramu")
    from app.database import Customer, get_db

    with get_db() as db:
        cust = db.query(Customer).filter_by(id=cid).first()
        assert cust.name == "Ramu"
        assert "<script>" not in cust.name

    # Malicious HTML in transaction item and message
    tx_id = add_transaction(
        owner_id=owner,
        customer_id=cid,
        customer_name="<script>alert(1)</script>Ramu",
        amount_paise=1000,
        item="<img src=x onerror=alert(1)>Soap",
        type_="Debit",
        message="<b>Test Msg</b>",
    )
    from app.database import Transaction

    with get_db() as db:
        tx = db.query(Transaction).filter_by(id=tx_id).first()
        assert tx.customer_name == "Ramu"
        assert tx.item_desc == "Soap"
        assert "<img" not in tx.item_desc
        assert tx.original_message == "Test Msg"
        assert "<b>" not in tx.original_message
