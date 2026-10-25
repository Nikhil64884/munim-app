"""
Mobile app endpoints: summary, export, account deletion.

Security:
  - Export filename is strictly validated against a whitelist pattern.
  - Path is resolved and confirmed to be inside EXPORTS_DIR.
"""

import datetime
import logging
import os
import re
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.config import config
from app.database import (
    AccountDeletion,
    Customer,
    Inventory,
    PendingContext,
    Transaction,
    get_db,
    summary_for_period,
)
from app.export_utils import generate_excel, get_owner_export_token
from app.money import format_inr
from app.privacy import mask_phone
from app.routes.auth import current_owner

logger = logging.getLogger("MunimJi.routes.mobile_api")

router = APIRouter(tags=["Mobile"])

TZ = ZoneInfo(config.TIMEZONE)

# Whitelist: hisaab_<8 hex owner token>_YYYY-MM-DD_HHMM_<12 hex>.xlsx
_EXPORT_FILENAME_RE = re.compile(
    r"^hisaab_[a-f0-9]{8}_\d{4}-\d{2}-\d{2}_\d{4}_[a-f0-9]{12}\.xlsx$"
)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
@router.get("/summary")
def summary(period: str = "today", owner_id: str = Depends(current_owner)):
    now = datetime.datetime.now(TZ)
    if period == "yesterday":
        start = (now - datetime.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end = start + datetime.timedelta(days=1)
    elif period == "week":
        start = (now - datetime.timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end = start + datetime.timedelta(days=7)
    elif period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (
            start.replace(month=start.month + 1)
            if start.month < 12
            else start.replace(year=start.year + 1, month=1)
        )
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + datetime.timedelta(days=1)

    start_utc = start.astimezone(datetime.UTC).replace(tzinfo=None)
    end_utc = end.astimezone(datetime.UTC).replace(tzinfo=None)
    s = summary_for_period(owner_id, start_utc, end_utc)

    return {
        "period": period,
        "udhaar_paise": s["udhaar_paise"],
        "jama_paise": s["jama_paise"],
        "cash_sale_paise": s["cash_sale_paise"],
        "udhaar_display": format_inr(s["udhaar_paise"]),
        "jama_display": format_inr(s["jama_paise"]),
        "cash_sale_display": format_inr(s["cash_sale_paise"]),
    }


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
@router.get("/export")
def export(owner_id: str = Depends(current_owner)):
    filename = generate_excel(owner_id)
    if not filename:
        raise HTTPException(500, "Export failed.")
    return {"filename": filename, "url": f"/api/export/{filename}"}


def _safe_export_path(filename: str, owner_id: str) -> str:
    """
    Resolve filename inside EXPORTS_DIR, guaranteeing it stays there and belongs to owner.

    Raises HTTPException(400) on malformed input, HTTPException(403) on owner mismatch.
    """
    # 1. Strict whitelist pattern
    if not _EXPORT_FILENAME_RE.match(filename):
        logger.warning(f"Rejected filename (pattern mismatch): {filename!r}")
        raise HTTPException(400, "Invalid filename.")

    # 2. Prevent IDOR: Confirm file belongs to the requesting owner
    expected_token = get_owner_export_token(owner_id)
    parts = filename.split("_")
    if len(parts) < 5 or parts[1] != expected_token:
        logger.warning(f"Rejected export access (owner mismatch): {filename!r} by owner={mask_phone(owner_id)}")
        raise HTTPException(403, "Access denied. You do not own this export file.")

    # 3. Normalize and confirm containment
    exports_root = os.path.realpath(config.EXPORTS_DIR)
    candidate = os.path.realpath(os.path.join(exports_root, filename))

    # Must start with exports_root + separator (prevents sibling-prefix tricks)
    if not candidate.startswith(exports_root + os.sep):
        logger.warning(f"Rejected filename (path escape): {filename!r}")
        raise HTTPException(400, "Invalid filename.")

    # 4. Must exist
    if not os.path.isfile(candidate):
        raise HTTPException(404, "File not found.")

    return candidate


@router.get("/export/{filename}")
def download_export(filename: str, owner_id: str = Depends(current_owner)):
    path = _safe_export_path(filename, owner_id)
    return FileResponse(path, filename=filename, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------------------------------------------------------------------------
# Account deletion (Play Store requirement)
# ---------------------------------------------------------------------------
@router.delete("/account")
def delete_account(owner_id: str = Depends(current_owner)):
    now_utc = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    with get_db() as db:
        # Scrub PII immediately on account deletion: the row is kept for the
        # 30-day retention window but no customer name / raw text survives.
        db.query(Transaction).filter_by(owner_id=owner_id).update(
            {
                "is_deleted": 1,
                "deleted_at": now_utc,
                "customer_id": None,
                "customer_name": "[ANONYMIZED]",
                "item_desc": "[ANONYMIZED]",
                "original_message": None,
            }
        )
        db.query(PendingContext).filter_by(sender_id=owner_id).delete()
        db.query(Inventory).filter_by(owner_id=owner_id).delete()
        db.query(Customer).filter_by(owner_id=owner_id).delete()
        db.add(AccountDeletion(owner_id=owner_id))
        db.commit()
    logger.warning(f"Account deleted: {mask_phone(owner_id)}")
    return {
        "status": "deleted",
        "retention_note": "Soft-deleted and anonymised; removed after 30 days.",
    }
