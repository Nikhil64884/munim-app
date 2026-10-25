"""
Utility script to migrate/normalize owner_id (phone numbers) across the database.
"""

import sys

from app.database import AccountDeletion, AuditLog, Customer, Inventory, PendingContext, Transaction, get_db
from app.routes.auth import _normalize_phone


def migrate_owner_phone(old_phone: str, new_phone: str):
    """Normalize or rename an owner phone number across all tables."""
    old_norm = _normalize_phone(old_phone)
    new_norm = _normalize_phone(new_phone)

    print(f"Migrating owner: {old_norm} -> {new_norm}")
    with get_db() as db:
        c = db.query(Customer).filter_by(owner_id=old_norm).update({"owner_id": new_norm})
        t = db.query(Transaction).filter_by(owner_id=old_norm).update({"owner_id": new_norm})
        inv = db.query(Inventory).filter_by(owner_id=old_norm).update({"owner_id": new_norm})
        
        # Avoid unique constraint violation if new_phone already has a pending context
        dest_context = db.query(PendingContext).filter_by(sender_id=new_norm).first()
        if dest_context:
            db.query(PendingContext).filter_by(sender_id=old_norm).delete()
            p = 0
        else:
            p = db.query(PendingContext).filter_by(sender_id=old_norm).update({"sender_id": new_norm})

        a = db.query(AuditLog).filter_by(owner_id=old_norm).update({"owner_id": new_norm})
        d = db.query(AccountDeletion).filter_by(owner_id=old_norm).update({"owner_id": new_norm})
        db.commit()
        print(f"Updated: {c} customers, {t} transactions, {inv} inventory items, {p} contexts, {a} audit logs, {d} deletions.")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python scripts/migrate_owner_ids.py <old_phone> <new_phone>")
        sys.exit(1)
    migrate_owner_phone(sys.argv[1], sys.argv[2])
