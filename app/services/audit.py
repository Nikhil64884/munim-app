"""
Audit logging — every important action recorded.

Usage:
    audit_log(db, owner_id, "create", "transaction", tx_id, new_value={...})
"""

import json
import logging

from app.database import AuditLog
from app.privacy import mask_phone

logger = logging.getLogger("MunimJi.audit")


def audit_log(
    db,
    owner_id: str,
    action: str,
    entity: str,
    entity_id: int | None = None,
    old_value: dict | None = None,
    new_value: dict | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
):
    """
    Record one audit entry.

    action: create | update | delete | login | logout | export
    entity: transaction | customer | account | auth
    """
    try:
        entry = AuditLog(
            owner_id=owner_id,
            action=action,
            entity=entity,
            entity_id=entity_id,
            old_value=json.dumps(old_value, default=str) if old_value else None,
            new_value=json.dumps(new_value, default=str) if new_value else None,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        db.add(entry)
        db.commit()
        logger.info(f"[AUDIT] {action} {entity}#{entity_id or '-'} by {mask_phone(owner_id)}")
    except Exception as e:
        # Audit failure must NEVER crash the main operation
        logger.error(f"Audit log failed: {e}", exc_info=True)
