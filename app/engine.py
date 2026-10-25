"""
Munim Ji AI — Shared Engine.

Single entry point for message processing. Both /api/chat and /api/voice
call `handle_message()`.

All amounts internally in PAISE (integer).
"""

import datetime
import json
import logging
from zoneinfo import ZoneInfo

from app.brain import process_text_input
from app.config import config
from app.database import (
    Customer,
    Transaction,
    add_transaction,
    consume_context,
    get_balance_paise,
    get_customer_id_by_name,
    get_db,
    get_last_transaction,
    get_or_create_customer,
    list_customers_with_balance,
    peek_context,
    save_context,
    soft_delete_transaction,
    summary_for_period,
)
from app.messages import msg
from app.money import format_inr, rupees_to_paise, validate_amount_paise
from app.services.amounts import extract_amount_rupees
from app.services.audit import audit_log
from app.services.customers import format_ambiguity_message, resolve_customer
from app.services.inventory import get_low_stock_alert
from app.services.reminder import (
    format_reminder_message,
    get_outstanding_customers,
    process_reminder_approval,
)
from app.services.transactions import (
    DECREASE_OWED,
    INCREASE_OWED,
    NEUTRAL,
    compose_saved_reply,
    direction_word,
    is_affirmative,
    is_negative,
)

logger = logging.getLogger("MunimJi.engine")

TZ = ZoneInfo(config.TIMEZONE)

# Intents that never write anything: safe to run while a pending context exists.
_READONLY_INTENTS = {
    "Query",
    "AllCustomers",
    "DateSummary",
    "Export",
    "Undo",
    "Reset",
    "InventoryRequest",
    "ReminderRequest",
}
# Pending contexts that need an explicit yes/no or a menu selection.
_SELECTION_PENDING = {"amount_confirmation", "reminder_approval", "name_disambiguation"}


def _is_complete_transaction(data: dict | None) -> bool:
    """True when the LLM parse already has everything needed to save a row."""
    if not data:
        return False
    tx_type = data.get("transaction_type")
    if tx_type not in INCREASE_OWED | DECREASE_OWED | NEUTRAL:
        return False
    try:
        amount = float(data.get("amount") or 0)
    except (TypeError, ValueError):
        return False
    if amount <= 0:
        return False
    return tx_type == "CashSale" or bool((data.get("customer_name") or "").strip())


def _confirmation_key(tx_type: str) -> str:
    """Pick the right confirmation wording for the transaction type."""
    if tx_type == "CashSale":
        return "CONFIRM_AMOUNT_CASH"
    if tx_type == "Sale":
        return "CONFIRM_AMOUNT_SALE"
    if tx_type in INCREASE_OWED:
        return "CONFIRM_AMOUNT_UDHAAR"
    return "CONFIRM_AMOUNT_JAMA"


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------
def handle_message(owner_id: str, text: str) -> dict:
    """
    Process one user message.

    Returns:
        {"reply": str, "media_url": str | None, "data": dict | None}
    """
    text = (text or "").strip()
    if not text:
        return {"reply": msg("GREETING"), "media_url": None, "data": None}

    ctx = peek_context(owner_id)
    if ctx:
        return _handle_with_context(owner_id, text, ctx)
    return _handle_fresh(owner_id, text)


# ---------------------------------------------------------------------------
# Fresh message
# ---------------------------------------------------------------------------
def _handle_fresh(owner_id: str, text: str, data: dict | None = None) -> dict:
    data = data if data is not None else process_text_input(text)
    if not data:
        return {"reply": msg("FALLBACK"), "media_url": None, "data": None}

    tx_type = data["transaction_type"]

    if tx_type == "Query":
        return _handle_query(owner_id, data)
    if tx_type == "AllCustomers":
        return _handle_all_customers(owner_id)
    if tx_type == "DateSummary":
        return _handle_date_summary(owner_id, data)
    if tx_type == "Export":
        return {"reply": "__EXPORT__", "media_url": None, "data": data}
    if tx_type == "Undo":
        return _handle_undo(owner_id)
    if tx_type == "Reset":
        return _handle_reset_request(owner_id)
    if tx_type == "Clarification":
        return _handle_clarification(owner_id, text, data)
    if tx_type == "ReminderRequest":
        return _handle_reminder_request(owner_id, data)
    if tx_type == "InventoryRequest":
        return _handle_inventory_request(owner_id, data)
    if tx_type == "General":
        return {
            "reply": data["reply_message"] or msg("DEFAULT_ERROR"),
            "media_url": None,
            "data": data,
        }
    if tx_type in INCREASE_OWED | DECREASE_OWED | NEUTRAL:
        return _handle_transaction(owner_id, text, data)

    return {"reply": msg("FALLBACK"), "media_url": None, "data": data}


def _handle_reset_request(owner_id: str) -> dict:
    save_context(owner_id, None, "Reset", "reset_confirmation", None)
    return {"reply": msg("RESET_CONFIRM"), "media_url": None, "data": None}


def _handle_reset_confirmation(owner_id: str, text: str) -> dict:
    consumed = consume_context(owner_id)
    if not is_affirmative(text) or consumed is None:
        return {"reply": msg("CONFIRM_CANCELLED"), "media_url": None, "data": None}
    now_utc = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        with get_db() as db:
            # Customers are hard-deleted, so detach the transactions that
            # reference them first (the foreign key is enforced by SQLite).
            db.query(Transaction).filter_by(owner_id=owner_id).update(
                {"is_deleted": 1, "deleted_at": now_utc, "customer_id": None}
            )
            db.query(Customer).filter_by(owner_id=owner_id).delete()
            audit_log(
                db,
                owner_id,
                "RESET_ALL_DATA",
                "account",
                new_value={"transactions": "soft-deleted", "customers": "deleted"},
            )
            db.commit()
        return {"reply": msg("RESET_SUCCESS"), "media_url": None, "data": None}
    return {"reply": msg("CONFIRM_CANCELLED"), "media_url": None, "data": None}


def _handle_reminder_request(owner_id: str, data: dict) -> dict:
    """Handle reminder request - show list and ask for approval"""
    customers = get_outstanding_customers(owner_id)

    if not customers:
        return {
            "reply": "Sabhi customers ka udhaar clear hai! 🎉 Koi yaad delai ki zaroorat nahi.",
            "media_url": None,
            "data": data
        }

    # Format the reminder list for shopkeeper review
    reminder_text = format_reminder_message(customers, owner_id)

    # Save context for approval flow (similar to existing confirmation system)
    save_context(
        owner_id,
        None,  # No specific customer
        "ReminderRequest",
        "reminder_approval",
        json.dumps({
            "customer_count": len(customers),
            "reminder_text": reminder_text
        })
    )

    return {
        "reply": reminder_text,
        "media_url": None,
        "data": data
    }


def _handle_inventory_request(owner_id: str, data: dict) -> dict:
    """Return the current low-stock alert for the authenticated owner."""
    result = get_low_stock_alert(owner_id)
    return {
        "reply": result["message"],
        "media_url": None,
        "data": {**data, "inventory": result},
    }


def _handle_reminder_approval(owner_id: str, text: str, ctx: dict) -> dict:
    """Handle the shopkeeper approval step for payment reminders."""
    try:
        payload = json.loads(ctx.get("pending_payload") or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}

    cleaned = text.strip().lower()
    if is_affirmative(cleaned):
        consumed = consume_context(owner_id)
        if consumed is None:
            return {"reply": msg("CONFIRM_CANCELLED"), "media_url": None, "data": None}
        result = process_reminder_approval(owner_id)
        if result.get("status") == "no_dues":
            return {"reply": result["message"], "media_url": None, "data": None}

        reply_lines = [
            f"✅ {result['total_customers']} customers ke liye reminder tayyar!",
            f"💰 Total rashi: ₹{result['total_amount_rupees']:.2f}",
            "",
            "📱 Apni Munim Ji app mein jaakar inhe bhejin:",
        ]
        for reminder in result["reminders"][:3]:
            reply_lines.append(
                f"• {reminder['customer_name']}: ₹{reminder['amount_rupees']:.2f}"
            )
        if len(result["reminders"]) > 3:
            reply_lines.append(f"• Aur {len(result['reminders']) - 3} dusre...")
        reply_lines.extend(["", "Note: UPI links apni app ke through bhejiye."])
        return {
            "reply": "\n".join(reply_lines),
            "media_url": None,
            "data": {"reminder_data": result},
        }

    if is_negative(cleaned):
        consume_context(owner_id)
        return {
            "reply": "Reminder nahi bhejaya. Kisi aur yaad delai ki zaroorat ho to bataiye.",
            "media_url": None,
            "data": None,
        }

    return {
        "reply": payload.get("reminder_text") or "Kya reminders bhejiye? 'haan' ya 'nahi' mein javab dijiye.",
        "media_url": None,
        "data": None,
    }
# ---------------------------------------------------------------------------
def _handle_clarification(owner_id: str, original_text: str, data: dict) -> dict:
    name = data.get("customer_name") or ""
    amount_rupees = float(data.get("amount") or 0)
    if amount_rupees == 0:
        regex_amt = extract_amount_rupees(original_text)
        if regex_amt is not None:
            amount_rupees = float(regex_amt)
    amount_paise = rupees_to_paise(amount_rupees) if amount_rupees > 0 else 0

    tx_type = data.get("transaction_type", "Debit")
    if tx_type == "Clarification":
        t_low = original_text.lower()
        if any(w in t_low for w in ["jama", "kam", "minus", "ghatao", "liye", "mila", "aagaya", "aaye"]):
            tx_type = "Credit"
        elif any(w in t_low for w in ["cash", "nakad"]):
            tx_type = "CashSale"
        else:
            tx_type = "Debit"

    payload = {
        "item_desc": data.get("item_desc", ""),
        "original_text": original_text,
        "customer_name": name,
        "amount": amount_rupees,
        "amount_paise": amount_paise,
        "transaction_type": tx_type,
    }
    save_context(owner_id, name or None, tx_type, "clarification", json.dumps(payload))
    reply = data.get("reply_message") or (
        msg("CLARIFICATION_NAME_REQUIRED") if not name else msg("CLARIFICATION_DEFAULT")
    )
    return {"reply": reply, "media_url": None, "data": data}


# ---------------------------------------------------------------------------
# With pending context
# ---------------------------------------------------------------------------
def _handle_with_context(owner_id: str, text: str, ctx: dict) -> dict:
    pending = ctx.get("pending_type", "clarification")

    if pending == "reset_confirmation":
        return _handle_reset_confirmation(owner_id, text)
    if pending == "clarification":
        return _handle_clarification_followup(owner_id, text, ctx)

    if pending in _SELECTION_PENDING:
        # A menu selection / yes-no answer belongs to the pending handler.
        if pending == "name_disambiguation":
            t = text.strip().lower()
            if t.isdigit() or t in {"naya", "new", "n", "0"}:
                return _handle_disambiguation(owner_id, text, ctx)
        elif is_negative(text):
            consume_context(owner_id)
            return {"reply": msg("CONFIRM_CANCELLED"), "media_url": None, "data": None}
        elif is_affirmative(text):
            if pending == "amount_confirmation":
                return _handle_confirmation(owner_id, text, ctx)
            return _handle_reminder_approval(owner_id, text, ctx)
        elif pending == "name_disambiguation" and is_negative(text):
            consume_context(owner_id)
            return {"reply": msg("CONFIRM_CANCELLED"), "media_url": None, "data": None}

        # Anything else is a *new* intent: don't trap the shopkeeper in the
        # old question. Cancel the pending context and process it normally.
        new_data = process_text_input(text)
        if _is_complete_transaction(new_data) or (
            new_data and new_data.get("transaction_type") in _READONLY_INTENTS
        ):
            consume_context(owner_id)
            return _handle_fresh(owner_id, text, new_data)

        # Reply was neither a selection nor a usable intent: re-prompt.
        if pending == "amount_confirmation":
            return _handle_confirmation(owner_id, text, ctx)
        if pending == "reminder_approval":
            return _handle_reminder_approval(owner_id, text, ctx)
        return _handle_disambiguation(owner_id, text, ctx)

    consume_context(owner_id)
    return _handle_fresh(owner_id, text)


def _handle_confirmation(owner_id: str, text: str, ctx: dict) -> dict:
    try:
        payload = json.loads(ctx.get("pending_payload") or "{}")
    except Exception:
        payload = {}

    if is_negative(text):
        consume_context(owner_id)
        return {"reply": msg("CONFIRM_CANCELLED"), "media_url": None, "data": None}

    if is_affirmative(text):
        consumed = consume_context(owner_id)
        if consumed is None:
            return {"reply": msg("CONFIRM_CANCELLED"), "media_url": None, "data": None}
        try:
            payload = json.loads(consumed.get("pending_payload") or "{}")
        except Exception:
            payload = {}
        amount_paise = int(payload.get("amount_paise") or 0)
        ok, reason = validate_amount_paise(amount_paise)
        if not ok:
            logger.warning(f"Invalid amount: {reason}")
            return {"reply": msg("TECHNICAL_ERROR"), "media_url": None, "data": None}
        try:
            add_transaction(
                owner_id=owner_id,
                customer_id=payload.get("customer_id"),
                customer_name=payload.get("customer_name"),
                amount_paise=amount_paise,
                item=payload.get("item_desc"),
                type_=payload.get("transaction_type"),
                message=payload.get("original_message", text),
            )
            reply = compose_saved_reply(
                payload.get("transaction_type", ""),
                payload.get("customer_id"),
                payload.get("customer_name") or "?",
                amount_paise,
                owner_id,
            )
            return {"reply": reply, "media_url": None, "data": None}
        except Exception as e:
            logger.error(f"Confirmed save failed: {e}", exc_info=True)
            return {"reply": msg("TECHNICAL_ERROR"), "media_url": None, "data": None}

    # Re-prompt with the same transaction that is still waiting for yes/no.
    amount_str = format_inr(int(payload.get("amount_paise") or 0))
    tx_type = payload.get("transaction_type", "")
    return {
        "reply": msg(
            _confirmation_key(tx_type),
            name=payload.get("customer_name") or "?",
            amount=amount_str,
        ),
        "media_url": None,
        "data": None,
    }


def _handle_disambiguation(owner_id: str, text: str, ctx: dict) -> dict:
    try:
        payload = json.loads(ctx.get("pending_payload") or "{}")
    except Exception:
        payload = {}
    options = payload.get("options") or []
    if not options:
        # Nothing to disambiguate (payload lost/corrupted) — start fresh.
        consume_context(owner_id)
        return _handle_fresh(owner_id, text)
    t = text.strip().lower()

    # Choose existing customer by number
    if t.isdigit():
        idx = int(t) - 1
        if 0 <= idx < len(options):
            chosen = options[idx]
            consumed = consume_context(owner_id)
            if consumed is None:
                return {"reply": msg("CONFIRM_CANCELLED"), "media_url": None, "data": None}
            try:
                add_transaction(
                    owner_id=owner_id,
                    customer_id=chosen["id"],
                    customer_name=chosen["name"],
                    amount_paise=int(payload.get("amount_paise") or 0),
                    item=payload.get("item_desc"),
                    type_=payload.get("transaction_type", "Debit"),
                    message=payload.get("original_message", text),
                )
                reply = compose_saved_reply(
                    payload.get("transaction_type", "Debit"),
                    chosen["id"],
                    chosen["name"],
                    int(payload.get("amount_paise") or 0),
                    owner_id,
                )
                return {"reply": reply, "media_url": None, "data": None}
            except Exception as e:
                logger.error(f"Disambiguation save failed: {e}", exc_info=True)
                return {"reply": msg("TECHNICAL_ERROR"), "media_url": None, "data": None}

    # Create new customer
    if t in {"naya", "new", "n", "0"}:
        consumed = consume_context(owner_id)
        if consumed is None:
            return {"reply": msg("CONFIRM_CANCELLED"), "media_url": None, "data": None}
        name = payload.get("name") or "Unknown"
        try:
            cid = get_or_create_customer(owner_id, name)
            add_transaction(
                owner_id=owner_id,
                customer_id=cid,
                customer_name=name,
                amount_paise=int(payload.get("amount_paise") or 0),
                item=payload.get("item_desc"),
                type_=payload.get("transaction_type", "Debit"),
                message=payload.get("original_message", text),
            )
            reply = compose_saved_reply(
                payload.get("transaction_type", "Debit"),
                cid,
                name,
                int(payload.get("amount_paise") or 0),
                owner_id,
            )
            return {"reply": reply, "media_url": None, "data": None}
        except Exception as e:
            logger.error(f"New customer save failed: {e}", exc_info=True)
            return {"reply": msg("TECHNICAL_ERROR"), "media_url": None, "data": None}

    return {"reply": format_ambiguity_message(options), "media_url": None, "data": None}


def _handle_clarification_followup(owner_id: str, text: str, ctx: dict) -> dict:
    try:
        payload = json.loads(ctx.get("pending_payload") or "{}")
    except Exception:
        payload = {}

    t_clean = text.strip()
    if is_negative(t_clean):
        consume_context(owner_id)
        return {"reply": msg("CONFIRM_CANCELLED"), "media_url": None, "data": None}

    # A new non-transaction intent cancels the pending clarification.
    new_data = process_text_input(t_clean)
    if new_data and new_data.get("transaction_type") in {
        "Query",
        "AllCustomers",
        "DateSummary",
        "Export",
        "Undo",
        "Reset",
        "InventoryRequest",
        "ReminderRequest",
    }:
        consume_context(owner_id)
        return _handle_fresh(owner_id, t_clean, new_data)

    prev_name = ctx.get("customer_name") or payload.get("customer_name") or ""
    prev_amount_paise = int(payload.get("amount_paise") or 0)
    prev_tx_type = ctx.get("transaction_type") or payload.get("transaction_type") or "Debit"
    if prev_tx_type == "Clarification":
        prev_tx_type = "Debit"
    prev_item = payload.get("item_desc") or ""
    prev_orig_text = payload.get("original_text") or ""

    new_name = (new_data.get("customer_name") if new_data else "") or ""
    new_amount_rupees = float((new_data.get("amount") if new_data else 0) or 0)
    regex_amt = extract_amount_rupees(t_clean)
    if regex_amt is not None and abs(float(regex_amt) - new_amount_rupees) > 0.5:
        new_amount_rupees = float(regex_amt)

    # A brand-new complete transaction for a *different* customer is a new
    # intent, not a refinement: save it and drop the stale pending one
    # instead of silently overwriting it.
    if _is_complete_transaction(new_data) and new_name.strip():
        if new_name.strip().lower() != prev_name.strip().lower():
            consume_context(owner_id)
            return _handle_fresh(owner_id, t_clean, new_data)

    new_amount_paise = rupees_to_paise(new_amount_rupees) if new_amount_rupees > 0 else 0
    new_tx_type = (new_data.get("transaction_type") if new_data else "") or ""
    new_item = (new_data.get("item_desc") if new_data else "") or ""

    final_name = new_name.strip() if new_name.strip() else prev_name.strip()
    final_amount_paise = new_amount_paise if new_amount_paise > 0 else prev_amount_paise

    if new_tx_type in {"Debit", "Credit", "Sale", "CashSale"}:
        final_tx_type = new_tx_type
    elif prev_tx_type in {"Debit", "Credit", "Sale", "CashSale"}:
        final_tx_type = prev_tx_type
    else:
        final_tx_type = "Debit"

    final_item = new_item or prev_item or ("Jama" if final_tx_type == "Credit" else "Udhar")

    # If still missing name
    if not final_name:
        save_context(
            owner_id,
            None,
            final_tx_type,
            "clarification",
            json.dumps({
                "item_desc": final_item,
                "original_text": f"{prev_orig_text} {t_clean}".strip(),
                "amount_paise": final_amount_paise,
                "transaction_type": final_tx_type,
            }),
        )
        return {"reply": msg("CLARIFICATION_NAME_REQUIRED"), "media_url": None, "data": new_data}

    # If still missing amount
    if final_amount_paise <= 0:
        save_context(
            owner_id,
            final_name,
            final_tx_type,
            "clarification",
            json.dumps({
                "customer_name": final_name,
                "item_desc": final_item,
                "original_text": f"{prev_orig_text} {t_clean}".strip(),
                "amount_paise": 0,
                "transaction_type": final_tx_type,
            }),
        )
        return {"reply": msg("CLARIFICATION_DEFAULT"), "media_url": None, "data": new_data}

    consume_context(owner_id)

    ok, reason = validate_amount_paise(final_amount_paise)
    if not ok:
        logger.warning(f"Invalid amount in clarification follow-up: {reason}")
        return {"reply": msg("FALLBACK"), "media_url": None, "data": new_data}

    synthetic_data = {
        "customer_name": final_name,
        "amount": final_amount_paise / 100.0,
        "item_desc": final_item,
        "transaction_type": final_tx_type,
        "reply_message": "",
    }
    combined_msg = f"{prev_orig_text} | {t_clean}" if prev_orig_text else t_clean

    resolution = resolve_customer(owner_id, final_name)
    if resolution["action"] == "ambiguous":
        payload_ambig = {
            "options": resolution["options"],
            "name": final_name,
            "amount_paise": final_amount_paise,
            "item_desc": final_item,
            "transaction_type": final_tx_type,
            "original_message": combined_msg,
        }
        save_context(owner_id, final_name, final_tx_type, "name_disambiguation", json.dumps(payload_ambig))
        return {
            "reply": format_ambiguity_message(resolution["options"]),
            "media_url": None,
            "data": synthetic_data,
        }

    cid = resolution["customer_id"]
    cname = resolution["name"]
    threshold = config.CONFIRM_AMOUNT_THRESHOLD * 100
    if final_amount_paise >= threshold:
        return _request_confirmation(owner_id, combined_msg, synthetic_data, final_amount_paise, cid, cname)
    return _save_transaction(owner_id, combined_msg, synthetic_data, final_amount_paise, cid, cname)


# ---------------------------------------------------------------------------
# Transaction
# ---------------------------------------------------------------------------
def _handle_transaction(owner_id: str, original_text: str, data: dict) -> dict:
    tx_type = data["transaction_type"]
    name = data.get("customer_name") or ""
    amount_rupees = float(data.get("amount") or 0)

    # Cross-verify against regex extraction
    regex_amt = extract_amount_rupees(original_text)
    if regex_amt is not None and abs(float(regex_amt) - amount_rupees) > 0.5:
        logger.warning(f"Amount mismatch LLM={amount_rupees} regex={regex_amt} — trusting regex")
        amount_rupees = float(regex_amt)

    amount_paise = rupees_to_paise(amount_rupees)
    ok, reason = validate_amount_paise(amount_paise)
    if not ok:
        logger.warning(f"Invalid amount: {reason}")
        return {"reply": msg("FALLBACK"), "media_url": None, "data": data}

    # CashSale doesn't need a customer
    if tx_type == "CashSale":
        threshold = config.CONFIRM_AMOUNT_THRESHOLD * 100
        if amount_paise >= threshold:
            return _request_confirmation(owner_id, original_text, data, amount_paise, None, None)
        return _save_transaction(owner_id, original_text, data, amount_paise, None, None)

    # Debit / Sale / Credit require a customer
    if not name:
        save_context(
            owner_id,
            None,
            tx_type,
            "clarification",
            json.dumps({
                "item_desc": data.get("item_desc", ""),
                "original_text": original_text,
                "amount": amount_rupees,
                "amount_paise": amount_paise,
                "transaction_type": tx_type,
            }),
        )
        return {"reply": msg("CLARIFICATION_NAME_REQUIRED"), "media_url": None, "data": data}

    resolution = resolve_customer(owner_id, name)

    if resolution["action"] == "ambiguous":
        payload = {
            "options": resolution["options"],
            "name": name,
            "amount_paise": amount_paise,
            "item_desc": data.get("item_desc"),
            "transaction_type": tx_type,
            "original_message": original_text,
        }
        save_context(owner_id, name, tx_type, "name_disambiguation", json.dumps(payload))
        return {
            "reply": format_ambiguity_message(resolution["options"]),
            "media_url": None,
            "data": data,
        }

    cid = resolution["customer_id"]
    cname = resolution["name"]
    threshold = config.CONFIRM_AMOUNT_THRESHOLD * 100
    if amount_paise >= threshold:
        return _request_confirmation(owner_id, original_text, data, amount_paise, cid, cname)
    return _save_transaction(owner_id, original_text, data, amount_paise, cid, cname)


def _request_confirmation(owner_id, original_text, data, amount_paise, cid, cname) -> dict:
    payload = {
        "amount_paise": amount_paise,
        "customer_id": cid,
        "customer_name": cname,
        "item_desc": data.get("item_desc"),
        "transaction_type": data["transaction_type"],
        "original_message": original_text,
    }
    save_context(
        owner_id,
        cname,
        data["transaction_type"],
        "amount_confirmation",
        json.dumps(payload),
        customer_id=cid,
    )
    amount_str = format_inr(amount_paise)
    tx_type = data["transaction_type"]
    return {
        "reply": msg(
            _confirmation_key(tx_type),
            name=cname or "?",
            amount=amount_str,
        ),
        "media_url": None,
        "data": data,
    }


def _save_transaction(owner_id, original_text, data, amount_paise, cid, cname) -> dict:
    try:
        add_transaction(
            owner_id=owner_id,
            customer_id=cid,
            customer_name=cname,
            amount_paise=amount_paise,
            item=data.get("item_desc"),
            type_=data["transaction_type"],
            message=original_text,
        )
        reply = compose_saved_reply(
            data["transaction_type"], cid, cname or "Walk-in", amount_paise, owner_id
        )
        return {"reply": reply, "media_url": None, "data": data}
    except Exception as e:
        logger.error(f"Save failed: {e}", exc_info=True)
        return {"reply": msg("TECHNICAL_ERROR"), "media_url": None, "data": data}


# ---------------------------------------------------------------------------
# Query / summary
# ---------------------------------------------------------------------------
def _handle_query(owner_id: str, data: dict) -> dict:
    name = data.get("customer_name") or ""
    if not name:
        return {"reply": msg("CLARIFICATION_NAME"), "media_url": None, "data": data}

    customer_id = get_customer_id_by_name(owner_id, name)
    if customer_id is None:
        return {
            "reply": msg("CUSTOMER_NOT_FOUND", name=name),
            "media_url": None,
            "data": data,
        }

    balance = get_balance_paise(owner_id, customer_id)
    if balance > 0:
        reply = msg("BALANCE_OWED", name=name, amount=format_inr(balance))
    elif balance < 0:
        reply = msg("BALANCE_ADVANCE", name=name, amount=format_inr(abs(balance)))
    else:
        reply = msg("BALANCE_NIL", name=name)
    return {"reply": reply, "media_url": None, "data": data}


def _handle_all_customers(owner_id: str) -> dict:
    customers = [c for c in list_customers_with_balance(owner_id) if c["balance_paise"] != 0]
    if not customers:
        return {"reply": msg("SUMMARY_EMPTY"), "media_url": None, "data": None}

    lines = [msg("SUMMARY_HEADER")]
    # Accurate total over ALL customers; only the first 20 are listed inline.
    total = sum(c["balance_paise"] for c in customers)
    for c in customers[:20]:
        lines.append(msg("SUMMARY_LINE", name=c["name"], balance=format_inr(c["balance_paise"])))
    if len(customers) > 20:
        lines.append(msg("SUMMARY_MORE", count=len(customers) - 20))
    lines.append(msg("SUMMARY_TOTAL", total=format_inr(total)))
    return {"reply": "".join(lines), "media_url": None, "data": None}


def _handle_date_summary(owner_id: str, data: dict) -> dict:
    item = (data.get("item_desc") or "today").lower()
    now = datetime.datetime.now(TZ)

    if item == "yesterday":
        start = (now - datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + datetime.timedelta(days=1)
        period = "Kal"
    elif item == "week":
        start = (now - datetime.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + datetime.timedelta(days=7)
        period = "Is hafte"
    elif item == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (
            start.replace(month=start.month + 1)
            if start.month < 12
            else start.replace(year=start.year + 1, month=1)
        )
        period = "Is mahine"
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + datetime.timedelta(days=1)
        period = "Aaj"

    start_utc = start.astimezone(datetime.UTC).replace(tzinfo=None)
    end_utc = end.astimezone(datetime.UTC).replace(tzinfo=None)
    s = summary_for_period(owner_id, start_utc, end_utc)

    lines = [
        msg("DATE_SUMMARY_HEADER", period=period),
        msg("DATE_SUMMARY_LINE", label="Udhaar diya", amount=format_inr(s["udhaar_paise"])),
        msg("DATE_SUMMARY_LINE", label="Jama aaya", amount=format_inr(s["jama_paise"])),
        msg("DATE_SUMMARY_LINE", label="Nakad bikri", amount=format_inr(s["cash_sale_paise"])),
    ]
    total = s["udhaar_paise"] + s["jama_paise"] + s["cash_sale_paise"]
    lines.append(msg("DATE_SUMMARY_TOTAL", amount=format_inr(total)))
    return {"reply": "".join(lines), "media_url": None, "data": data}


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------
def _handle_undo(owner_id: str) -> dict:
    last = get_last_transaction(owner_id)
    if not last:
        return {"reply": msg("UNDO_NOTHING"), "media_url": None, "data": None}

    now_utc = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    age = (now_utc - last["created_at"]).total_seconds() / 60
    if age > config.UNDO_WINDOW_MINUTES:
        return {"reply": msg("UNDO_TOO_OLD"), "media_url": None, "data": None}

    if soft_delete_transaction(last["id"], owner_id):
        return {
            "reply": msg(
                "UNDO_SUCCESS",
                name=last["customer_name"],
                amount=format_inr(last["amount_paise"]),
                direction=direction_word(last["transaction_type"]),
            ),
            "media_url": None,
            "data": None,
        }
    return {"reply": msg("UNDO_NOTHING"), "media_url": None, "data": None}
