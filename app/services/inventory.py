"""Inventory management helpers."""

from app.database import (
    Inventory,
    add_or_update_inventory_item,
    get_db,
    get_inventory_item,
    get_low_stock_items,
    update_inventory_stock,
)


def get_low_stock_alert(owner_id: str) -> dict:
    """Return a user-facing alert and structured low-stock data."""
    low_stock_items = get_low_stock_items(owner_id)
    if not low_stock_items:
        return {
            "status": "none",
            "message": "Sab items ka stock theek hai. Low stock alert nahi.",
            "items": [],
        }

    lines = ["📦 Low Stock Alert!"]
    for item in low_stock_items:
        unit = f" ({item['unit']})" if item["unit"] else ""
        lines.append(
            f"• {item['item_name']}: {item['current_stock']}{unit} "
            f"(threshold: {item['low_stock_threshold']}{unit})"
        )
    lines.extend(["", "Stock badhao ya threshold update karne ke liye bataye."])
    return {
        "status": "low_stock",
        "message": "\n".join(lines),
        "items": low_stock_items,
    }


def process_inventory_update(owner_id: str, item_name: str, quantity: int) -> dict:
    """Apply a stock delta and return the updated item."""
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        return {"status": "error", "message": "Quantity poori sankhya honi chahiye."}

    item_name = (item_name or "").strip()
    if not item_name:
        return {"status": "error", "message": "Item ka naam zaroori hai."}

    if not update_inventory_stock(owner_id, item_name, quantity):
        return {
            "status": "error",
            "message": f"Item '{item_name}' nahi mila. Pehle item add karo.",
        }

    item = get_inventory_item(owner_id, item_name)
    if not item:
        return {"status": "error", "message": "Item mila par fetch nahi hua."}

    action = "joda" if quantity >= 0 else "ghataya"
    return {
        "status": "success",
        "message": (
            f"✅ {item['item_name']} ka stock {abs(quantity)} {item['unit'] or ''} {action}. "
            f"Abhi stock: {item['current_stock']} {item['unit'] or ''}."
        ),
        "item": item,
    }


def set_low_stock_threshold(owner_id: str, item_name: str, threshold: int) -> dict:
    """Set the low-stock threshold for an existing item."""
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 0:
        return {"status": "error", "message": "Threshold zero ya positive number hona chahiye."}

    item_name = (item_name or "").strip()
    item = get_inventory_item(owner_id, item_name)
    if not item:
        return {
            "status": "error",
            "message": f"Item '{item_name}' nahi mila. Pehle item add karo.",
        }

    with get_db() as db:
        row = db.query(Inventory).filter_by(owner_id=owner_id, item_name=item_name).first()
        if row is None:
            return {"status": "error", "message": "Item mila par update nahi hua."}
        row.low_stock_threshold = threshold
        db.commit()

    return {
        "status": "success",
        "message": (
            f"✅ {item['item_name']} ka low stock threshold {threshold} set kiya. "
            f"Abhi stock: {item['current_stock']}."
        ),
        "item": {
            **item,
            "low_stock_threshold": threshold,
        },
    }


__all__ = [
    "add_or_update_inventory_item",
    "get_low_stock_alert",
    "process_inventory_update",
    "set_low_stock_threshold",
]
