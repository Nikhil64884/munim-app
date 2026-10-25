"""Customer resolution — handles name ambiguity."""

import logging

from app.database import find_similar_customers, get_or_create_customer
from app.messages import msg

logger = logging.getLogger("MunimJi.services.customers")

AMBIGUITY_THRESHOLD = 2


def resolve_customer(owner_id: str, name: str) -> dict:
    """
    Decide what to do with a customer name.

    Returns:
        {"action": "exact",     "customer_id": int, "name": str}
        {"action": "ambiguous", "options": [...],   "name": str}
        {"action": "new",       "customer_id": int, "name": str}
    """
    name = (name or "").strip()
    if not name:
        return {"action": "ambiguous", "options": [], "name": ""}

    similar = find_similar_customers(owner_id, name)

    # Exact match?
    for c in similar:
        if c["name"].lower() == name.lower():
            return {"action": "exact", "customer_id": c["id"], "name": c["name"]}

    # Too many similar — ask user
    if len(similar) >= AMBIGUITY_THRESHOLD:
        return {"action": "ambiguous", "options": similar[:5], "name": name}

    # Single prefix match — reuse
    # Exact match only - do not silently match prefix like "Ram" to "Ramesh Kumar"
    if len(similar) == 1 and similar[0]["name"].strip().lower() == name.strip().lower():
        return {"action": "exact", "customer_id": similar[0]["id"], "name": similar[0]["name"]}

    # New customer
    cid = get_or_create_customer(owner_id, name)
    return {"action": "new", "customer_id": cid, "name": name}


def format_ambiguity_message(options: list[dict]) -> str:
    if not options:
        # Nothing to choose from (e.g. a lost payload): ask for the name again.
        return msg("CLARIFICATION_NAME")
    lines = [f"{i}. {c['name']}" for i, c in enumerate(options, start=1)]
    return msg("NAME_AMBIGUOUS", options="\n".join(lines))
