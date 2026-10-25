"""Customer-facing APIs for the mobile app."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.database import (
    AuditLog,
    Transaction,
    get_balance_paise,
    get_customer_ledger,
    get_db,
    get_or_create_customer,
    list_customers_with_balance,
    merge_customers,
    soft_delete_transaction,
    update_transaction,
)
from app.money import format_inr
from app.routes.auth import current_owner
from app.services.audit import audit_log

logger = logging.getLogger("MunimJi.routes.customers")

router = APIRouter(tags=["Customers"])


class CreateCustomerReq(BaseModel):
    name: str = Field(max_length=100)
    phone: str | None = Field(default=None, max_length=20)


@router.post("/customers")
def create_customer(req: CreateCustomerReq, owner_id: str = Depends(current_owner)):
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(400, "Customer name cannot be empty.")
    customer_id = get_or_create_customer(owner_id, name, phone=req.phone)
    balance = get_balance_paise(owner_id, customer_id)
    return {
        "success": True,
        "id": customer_id,
        "name": name,
        "phone": (req.phone or "").strip(),
        "balance_paise": balance,
        "balance_display": format_inr(balance),
    }


@router.get("/customers")
def list_customers(owner_id: str = Depends(current_owner)):
    rows = list_customers_with_balance(owner_id)
    return {
        "customers": [
            {
                "id": r["id"],
                "name": r["name"],
                "balance_paise": r["balance_paise"],
                "balance_display": format_inr(r["balance_paise"]),
            }
            for r in rows
        ]
    }


@router.get("/customers/{customer_id}/ledger")
def customer_ledger(customer_id: int, owner_id: str = Depends(current_owner)):
    data = get_customer_ledger(owner_id, customer_id)
    if not data:
        raise HTTPException(404, "Customer not found.")
    data["balance_display"] = format_inr(data["balance_paise"])
    for e in data["entries"]:
        e["amount_display"] = format_inr(e["amount_paise"])
    return data


class TransactionPatch(BaseModel):
    amount_paise: int | None = None
    item_desc: str | None = Field(default=None, max_length=200)
    transaction_type: str | None = Field(default=None, max_length=20)


@router.patch("/transactions/{tx_id}")
def patch_transaction(
    tx_id: int,
    patch: TransactionPatch,
    request: Request,
    owner_id: str = Depends(current_owner),
):
    # Fetch old value
    with get_db() as db:
        old = (
            db.query(Transaction)
            .filter_by(id=tx_id, owner_id=owner_id, is_deleted=0)
            .first()
        )
        if not old:
            raise HTTPException(404, "Transaction not found.")
        old_value = {
            "amount_paise": old.amount_paise,
            "item_desc": old.item_desc,
            "transaction_type": old.transaction_type,
        }

    try:
        ok = update_transaction(
            tx_id,
            owner_id,
            amount_paise=patch.amount_paise,
            item=patch.item_desc,
            type_=patch.transaction_type,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    if not ok:
        raise HTTPException(404, "Transaction not found.")

    # Audit
    with get_db() as db:
        new_value = dict(old_value)
        if patch.amount_paise is not None:
            new_value["amount_paise"] = patch.amount_paise
        if patch.item_desc is not None:
            new_value["item_desc"] = patch.item_desc
        if patch.transaction_type is not None:
            new_value["transaction_type"] = patch.transaction_type

        audit_log(
            db=db,
            owner_id=owner_id,
            action="update",
            entity="transaction",
            entity_id=tx_id,
            old_value=old_value,
            new_value=new_value,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )

    return {"status": "updated"}


@router.delete("/transactions/{tx_id}")
def delete_transaction(
    tx_id: int,
    request: Request,
    owner_id: str = Depends(current_owner),
):
    if not soft_delete_transaction(tx_id, owner_id):
        raise HTTPException(404, "Transaction not found.")

    with get_db() as db:
        audit_log(
            db=db,
            owner_id=owner_id,
            action="delete",
            entity="transaction",
            entity_id=tx_id,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    return {"status": "deleted"}


class MergeReq(BaseModel):
    from_id: int
    into_id: int


@router.post("/customers/merge")
def merge(req: MergeReq, owner_id: str = Depends(current_owner)):
    try:
        moved = merge_customers(owner_id, req.from_id, req.into_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return {"status": "merged", "moved_transactions": moved}


@router.get("/audit")
def get_audit_log(
    limit: int = Query(100, ge=1, le=500),  # noqa: B008
    owner_id: str = Depends(current_owner),
):
    """Owner's own audit log."""
    with get_db() as db:
        rows = (
            db.query(AuditLog)
            .filter(AuditLog.owner_id == owner_id)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
            .all()
        )
        return {
            "entries": [
                {
                    "id": r.id,
                    "action": r.action,
                    "entity": r.entity,
                    "entity_id": r.entity_id,
                    "old_value": r.old_value,
                    "new_value": r.new_value,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]
        }
