"""
Utility script to reset/clear database data.
Usage:
    python scripts/reset_db.py            # Clears all test transactions & customers
    python scripts/reset_db.py <phone>    # Clears data only for specific owner phone
"""

import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import (
    AccountDeletion,
    AuditLog,
    Customer,
    Inventory,
    PendingContext,
    ProcessedMessage,
    Transaction,
    get_db,
)


def reset_database(owner_id: str | None = None):
    with get_db() as db:
        if owner_id:
            t = db.query(Transaction).filter_by(owner_id=owner_id).delete()
            c = db.query(Customer).filter_by(owner_id=owner_id).delete()
            p = db.query(PendingContext).filter_by(sender_id=owner_id).delete()
            inv = db.query(Inventory).filter_by(owner_id=owner_id).delete()
            a = db.query(AuditLog).filter_by(owner_id=owner_id).delete()
            d = db.query(AccountDeletion).filter_by(owner_id=owner_id).delete()
            print(f"Cleared for owner {owner_id}: {t} transactions, {c} customers, {inv} items, {p} contexts, {a} audit logs, {d} deletions.")
        else:
            t = db.query(Transaction).delete()
            c = db.query(Customer).delete()
            p = db.query(PendingContext).delete()
            inv = db.query(Inventory).delete()
            m = db.query(ProcessedMessage).delete()
            a = db.query(AuditLog).delete()
            d = db.query(AccountDeletion).delete()
            print(f"Cleared ALL data: {t} transactions, {c} customers, {inv} items, {p} contexts, {m} messages, {a} audit logs, {d} deletions.")
        db.commit()


if __name__ == "__main__":
    # Ignore flags such as --force so they are never mistaken for a phone number.
    positional = [a for a in sys.argv[1:] if not a.startswith("-")]

    if "--force" not in sys.argv:
        print("=" * 60)
        print(" WARNING: This will permanently ERASE all Munim Ji data!")
        print("=" * 60)
        confirm = input("Are you sure? Type 'YES' to proceed: ").strip()
        if confirm != "YES":
            print("Operation cancelled.")
            sys.exit(0)

    target = positional[0] if positional else None
    reset_database(target)
