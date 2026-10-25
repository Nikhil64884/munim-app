"""
Munim Ji AI — Database Layer (v3.1).

Tables:
  - Customer          : canonical customer per owner (case-insensitive)
  - Transaction       : credit/debit/sale records in PAISE (integer)
  - PendingContext    : clarification / confirmation / name-disambiguation
  - ProcessedMessage  : idempotency for client_msg_id
  - AccountDeletion   : audit trail for DELETE /api/account
  - AuditLog          : kaun, kab, kya badla

All amounts stored as INTEGER PAISE (never float).
"""

import datetime
import logging
import os
from contextlib import contextmanager

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    delete,
    event,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

from app.config import config
from app.money import validate_amount_paise
from app.privacy import mask_phone

logger = logging.getLogger("MunimJi.database")

# ---------------------------------------------------------------------------
# Constants & Helpers
# ---------------------------------------------------------------------------
VALID_TRANSACTION_TYPES = {"Debit", "Sale", "CashSale", "Credit"}


def utc_now() -> datetime.datetime:
    """Return timezone-naive UTC datetime for database compatibility."""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def sanitize_input_text(val: str | None, max_len: int = 150) -> str:
    """Strip HTML/script tags and dangerous control characters."""
    if not val:
        return ""
    import re

    # Remove script and style blocks including their contents
    cleaned = re.sub(
        r"<(script|style)[^>]*>.*?</\1>", "", str(val), flags=re.IGNORECASE | re.DOTALL
    )
    # Remove any remaining HTML tags
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    # Strip non-printable control characters
    cleaned = "".join(c for c in cleaned if c.isprintable() or c in " \t\n")
    return cleaned.strip()[:max_len]


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
def _sqlite_file_path(url: str) -> str | None:
    """Local file path for a sqlite:// URL, or None for memory/other drivers."""
    if not url.startswith("sqlite"):
        return None
    path = url.split("sqlite:///")[-1]
    if not path or path.startswith(":memory:"):
        return None
    return path


def _ensure_sqlite_dir(url: str) -> None:
    """Create the parent directory so a fresh checkout can boot (data/ is gitignored)."""
    path = _sqlite_file_path(url)
    if not path:
        return
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def _build_engine():
    url = config.DATABASE_URL
    kwargs = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        _ensure_sqlite_dir(url)
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    return create_engine(url, **kwargs)


engine = _build_engine()


if config.DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        """WAL mode + busy timeout for SQLite concurrency."""
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA foreign_keys=ON")
        finally:
            cur.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


@contextmanager
def get_db():
    """Yield a session. Rolls back on exception, always closes."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(String, index=True, nullable=False)
    name = Column(String, nullable=False)
    name_lower = Column(String, nullable=False, index=True)
    phone = Column(String, nullable=True)
    party_type = Column(String, default="customer", server_default="customer", nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("owner_id", "name_lower", name="uq_owner_customer"),
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(String, index=True, nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), index=True, nullable=True)
    customer_name = Column(String, index=True)
    date = Column(DateTime, default=utc_now, nullable=False, index=True)
    item_desc = Column(String)
    amount_paise = Column(BigInteger, nullable=False)
    transaction_type = Column(String, nullable=False)
    original_message = Column(String)
    is_deleted = Column(Integer, default=0, nullable=False, index=True)
    deleted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(
        DateTime,
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    customer = relationship("Customer", backref="transactions")


class PendingContext(Base):
    __tablename__ = "pending_contexts"

    id = Column(Integer, primary_key=True, index=True)
    sender_id = Column(String, unique=True, index=True, nullable=False)
    customer_name = Column(String)
    customer_id = Column(Integer, nullable=True)
    transaction_type = Column(String, default="Debit", nullable=False)
    pending_type = Column(String, default="clarification", nullable=False)
    pending_payload = Column(String, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)


class ProcessedMessage(Base):
    __tablename__ = "processed_messages"

    id = Column(Integer, primary_key=True, index=True)
    message_sid = Column(String, index=True, nullable=False)
    processed_at = Column(DateTime, default=utc_now, nullable=False)

    __table_args__ = (UniqueConstraint("message_sid", name="uq_message_sid"),)


class AccountDeletion(Base):
    __tablename__ = "account_deletions"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(String, index=True, nullable=False)
    requested_at = Column(DateTime, default=utc_now, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(String, index=True, nullable=False)
    action = Column(String, nullable=False)
    entity = Column(String, nullable=False)
    entity_id = Column(Integer, nullable=True)
    old_value = Column(String, nullable=True)
    new_value = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    created_at = Column(
        DateTime, default=utc_now, nullable=False, index=True
    )


# ---------------------------------------------------------------------------
# Inventory Management
# ---------------------------------------------------------------------------

class Inventory(Base):
    __tablename__ = "inventory"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(String, index=True, nullable=False)
    item_name = Column(String, nullable=False)
    current_stock = Column(Integer, nullable=False, default=0)
    low_stock_threshold = Column(Integer, nullable=False, default=0)
    unit = Column(String, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("owner_id", "item_name", name="uq_owner_item"),
    )


def add_or_update_inventory_item(owner_id: str, item_name: str, current_stock: int, low_stock_threshold: int, unit: str | None = None) -> None:
    """Add a new inventory item or update an existing one."""
    with get_db() as db:
        row = db.query(Inventory).filter_by(owner_id=owner_id, item_name=item_name).first()
        if row:
            row.current_stock = current_stock
            row.low_stock_threshold = low_stock_threshold
            row.unit = unit
        else:
            db.add(Inventory(
                owner_id=owner_id,
                item_name=item_name,
                current_stock=current_stock,
                low_stock_threshold=low_stock_threshold,
                unit=unit
            ))
        db.commit()


def get_low_stock_items(owner_id: str) -> list[dict]:
    """Return list of inventory items where current_stock <= low_stock_threshold."""
    with get_db() as db:
        rows = db.query(Inventory).filter_by(owner_id=owner_id).filter(Inventory.current_stock <= Inventory.low_stock_threshold).all()
        return [
            {
                "id": r.id,
                "item_name": r.item_name,
                "current_stock": r.current_stock,
                "low_stock_threshold": r.low_stock_threshold,
                "unit": r.unit,
            }
            for r in rows
        ]


def get_inventory_item(owner_id: str, item_name: str) -> dict | None:
    """Get a single inventory item by name."""
    with get_db() as db:
        row = db.query(Inventory).filter_by(owner_id=owner_id, item_name=item_name).first()
        if not row:
            return None
        return {
            "id": row.id,
            "item_name": row.item_name,
            "current_stock": row.current_stock,
            "low_stock_threshold": row.low_stock_threshold,
            "unit": row.unit,
        }


def update_inventory_stock(owner_id: str, item_name: str, quantity_change: int) -> bool:
    """Update the stock of an item by adding quantity_change (can be negative)."""
    with get_db() as db:
        row = db.query(Inventory).filter_by(owner_id=owner_id, item_name=item_name).first()
        if not row:
            return False
        if row.current_stock + quantity_change < 0:
            raise ValueError(f"Insufficient stock for '{item_name}': available {row.current_stock}, requested change {quantity_change}")
        row.current_stock += quantity_change
        db.commit()
        return True


# ---------------------------------------------------------------------------
# Init & Migration
# ---------------------------------------------------------------------------
def init_db():
    """Create all tables if missing."""
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialised.")


def migrate_legacy_data():
    """Idempotent migration. Safe on every startup."""
    with get_db() as db:
        _migrate_amount_column(db)
        _migrate_party_type_column(db)
        _migrate_phone_column(db)
        _migrate_customers(db)


def _migrate_phone_column(db):
    try:
        cols = [r[1] for r in db.execute(text("PRAGMA table_info(customers)")).fetchall()]
    except Exception:
        return
    if "phone" not in cols:
        db.execute(text("ALTER TABLE customers ADD COLUMN phone VARCHAR"))
        db.commit()
        logger.info("Added phone column to customers.")


def _migrate_party_type_column(db):
    try:
        cols = [r[1] for r in db.execute(text("PRAGMA table_info(customers)")).fetchall()]
    except Exception:
        return
    if "party_type" not in cols:
        db.execute(text("ALTER TABLE customers ADD COLUMN party_type VARCHAR DEFAULT 'customer'"))
        db.commit()
        logger.info("Added party_type column to customers.")
    db.execute(
        text(
            "UPDATE customers SET party_type = 'customer' "
            "WHERE party_type IS NULL OR party_type = ''"
        )
    )
    db.commit()


def _migrate_amount_column(db):
    try:
        cols = [r[1] for r in db.execute(text("PRAGMA table_info(transactions)")).fetchall()]
    except Exception:
        return
    if "amount_paise" not in cols:
        db.execute(text("ALTER TABLE transactions ADD COLUMN amount_paise BIGINT"))
        logger.info("Added amount_paise column.")
    if "amount" in cols:
        db.execute(
            text(
                "UPDATE transactions SET amount_paise = CAST(ROUND(amount * 100) AS INTEGER) "
                "WHERE amount_paise IS NULL AND amount IS NOT NULL"
            )
        )
        db.commit()
        logger.info("Backfilled amount_paise from legacy amount.")


def _migrate_customers(db):
    rows = db.execute(
        text(
            "SELECT DISTINCT owner_id, customer_name FROM transactions "
            "WHERE customer_id IS NULL AND customer_name IS NOT NULL AND customer_name != ''"
        )
    ).fetchall()
    if not rows:
        return
    for owner_id, raw_name in rows:
        norm = (raw_name or "").strip()
        if not norm:
            continue
        lower = norm.lower()
        existing = db.execute(
            text("SELECT id FROM customers WHERE owner_id=:o AND name_lower=:n"),
            {"o": owner_id, "n": lower},
        ).fetchone()
        if existing:
            cid = existing[0]
        else:
            db.execute(
                text(
                    "INSERT INTO customers (owner_id, name, name_lower, created_at) "
                    "VALUES (:o, :n, :nl, :t)"
                ),
                {"o": owner_id, "n": norm, "nl": lower, "t": utc_now()},
            )
            cid = db.execute(
                text("SELECT id FROM customers WHERE owner_id=:o AND name_lower=:n"),
                {"o": owner_id, "n": lower},
            ).fetchone()[0]
        db.execute(
            text(
                "UPDATE transactions SET customer_id=:cid WHERE owner_id=:o "
                "AND lower(customer_name)=:nl AND customer_id IS NULL"
            ),
            {"cid": cid, "o": owner_id, "nl": lower},
        )
    db.commit()
    logger.info(f"Migrated {len(rows)} distinct customer name(s).")


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------
def mark_message_processed(message_sid: str) -> bool:
    """Race-safe first-sight check. True = first time, False = duplicate."""
    if not message_sid:
        return True
    with get_db() as db:
        if db.query(ProcessedMessage).filter_by(message_sid=message_sid).first():
            return False
        try:
            db.add(ProcessedMessage(message_sid=message_sid))
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
            return False


def forget_message_processed(message_sid: str) -> None:
    """Remove an idempotency marker when message processing fails."""
    if not message_sid:
        return
    with get_db() as db:
        db.query(ProcessedMessage).filter_by(message_sid=message_sid).delete()
        db.commit()


def cleanup_old_processed_messages(older_than_hours: int = 48):
    cutoff = utc_now() - datetime.timedelta(hours=older_than_hours)
    with get_db() as db:
        n = db.query(ProcessedMessage).filter(ProcessedMessage.processed_at < cutoff).delete()
        db.commit()
        if n:
            logger.info(f"Cleaned {n} processed messages.")


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------
def get_or_create_customer(
    owner_id: str,
    name: str,
    party_type: str = "customer",
    phone: str | None = None,
) -> int:
    """Return customer_id. Case-insensitive. Creates if missing."""
    name = sanitize_input_text(name, max_len=100)
    if not name:
        raise ValueError("Customer name cannot be empty.")
    party_type = (party_type or "customer").strip().lower()
    if party_type not in {"customer", "supplier", "other"}:
        party_type = "customer"
    phone = sanitize_input_text(phone, max_len=20) or None
    lower = name.lower()
    with get_db() as db:
        row = db.execute(
            text("SELECT id, phone FROM customers WHERE owner_id=:o AND name_lower=:n"),
            {"o": owner_id, "n": lower},
        ).fetchone()
        if row:
            if phone and (row[1] or "") != phone:
                db.execute(
                    text("UPDATE customers SET phone=:p WHERE id=:i"),
                    {"p": phone, "i": row[0]},
                )
                db.commit()
            return row[0]
        try:
            db.execute(
                text(
                    "INSERT INTO customers (owner_id, name, name_lower, party_type, phone, created_at) "
                    "VALUES (:o, :n, :nl, :pt, :ph, :t)"
                ),
                {
                    "o": owner_id,
                    "n": name,
                    "nl": lower,
                    "pt": party_type,
                    "ph": phone,
                    "t": utc_now(),
                },
            )
            db.commit()
        except IntegrityError:
            db.rollback()
        row = db.execute(
            text("SELECT id FROM customers WHERE owner_id=:o AND name_lower=:n"),
            {"o": owner_id, "n": lower},
        ).fetchone()
        return row[0]


def find_similar_customers(owner_id: str, name: str, limit: int = 5) -> list[dict]:
    """Find customers whose name starts with or contains the given prefix."""
    lower = (name or "").strip().lower()
    if not lower:
        return []
    with get_db() as db:
        rows = db.execute(
            text(
                "SELECT id, name FROM customers WHERE owner_id=:o "
                "AND (name_lower LIKE :p OR name_lower LIKE :c) "
                "ORDER BY length(name) ASC LIMIT :l"
            ),
            {"o": owner_id, "p": f"{lower}%", "c": f"%{lower}%", "l": limit},
        ).fetchall()
        return [{"id": r[0], "name": r[1]} for r in rows]


def list_customers_with_balance(owner_id: str, party_type: str | None = None) -> list[dict]:
    """Every customer with net balance (paise). Sorted by owed DESC."""
    query = """
            SELECT c.id, c.name, COALESCE(c.party_type, 'customer') AS party_type,
              COALESCE(SUM(CASE WHEN t.transaction_type IN ('Debit','Sale')
                                THEN t.amount_paise ELSE 0 END), 0) AS owed,
              COALESCE(SUM(CASE WHEN t.transaction_type = 'Credit'
                                THEN t.amount_paise ELSE 0 END), 0) AS paid
            FROM customers c
            LEFT JOIN transactions t
              ON t.customer_id = c.id AND t.is_deleted = 0
            WHERE c.owner_id = :o
    """
    params = {"o": owner_id}
    if party_type and party_type.strip().lower() != "all":
        query += " AND LOWER(COALESCE(c.party_type, 'customer')) = :pt"
        params["pt"] = party_type.strip().lower()

    query += """
            GROUP BY c.id, c.name, c.party_type
            ORDER BY (owed - paid) DESC
    """
    with get_db() as db:
        rows = db.execute(text(query), params).fetchall()
    return [
        {
            "id": r[0],
            "name": r[1],
            "party_type": r[2] or "customer",
            "balance_paise": int((r[3] or 0) - (r[4] or 0)),
        }
        for r in rows
    ]


def merge_customers(owner_id: str, from_id: int, into_id: int) -> int:
    """Repoint all transactions from one customer to another. Returns moved count."""
    if from_id == into_id:
        return 0
    with get_db() as db:
        src = db.query(Customer).filter_by(id=from_id, owner_id=owner_id).first()
        dst = db.query(Customer).filter_by(id=into_id, owner_id=owner_id).first()
        if not src or not dst:
            raise ValueError("Customer not found for this owner")
        moved = (
            db.query(Transaction)
            .filter(Transaction.customer_id == from_id, Transaction.owner_id == owner_id)
            .update({"customer_id": into_id, "customer_name": dst.name})
        )
        db.delete(src)
        db.commit()
        logger.info(f"Merged customer {from_id}→{into_id}, moved {moved} txns")
        return moved


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------
def add_transaction(
    owner_id: str,
    customer_id: int | None,
    customer_name: str | None,
    amount_paise: int,
    item: str | None,
    type_: str,
    message: str,
) -> int:
    """Insert a transaction. Returns its id."""
    if type_ not in VALID_TRANSACTION_TYPES:
        raise ValueError(f"Invalid transaction_type '{type_}'.")
    if isinstance(amount_paise, bool) or not isinstance(amount_paise, int) or amount_paise <= 0:
        raise ValueError(f"amount_paise must be a positive integer, got {amount_paise!r}")

    sanitized_cust = sanitize_input_text(customer_name or "Walk-in", max_len=100) or "Walk-in"
    sanitized_item = sanitize_input_text(item, max_len=200) if item else None
    sanitized_msg = sanitize_input_text(message, max_len=500) if message else None

    with get_db() as db:
        t = Transaction(
            owner_id=owner_id,
            customer_id=customer_id,
            customer_name=sanitized_cust,
            amount_paise=amount_paise,
            item_desc=sanitized_item,
            transaction_type=type_,
            original_message=sanitized_msg,
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        logger.info(
            f"Tx id={t.id} owner={mask_phone(owner_id)} cust=*** "
            f"paise={amount_paise} type={type_}"
        )
        return t.id


def get_balance_paise(owner_id: str, customer_id: int) -> int:
    """Net owed in paise (positive = customer owes shopkeeper)."""
    with get_db() as db:
        row = db.execute(
            text(
                """
            SELECT
              COALESCE(SUM(CASE WHEN transaction_type IN ('Debit','Sale')
                                THEN amount_paise ELSE 0 END), 0)
              -
              COALESCE(SUM(CASE WHEN transaction_type = 'Credit'
                                THEN amount_paise ELSE 0 END), 0)
            FROM transactions
            WHERE owner_id=:o AND customer_id=:c AND is_deleted=0
        """
            ),
            {"o": owner_id, "c": customer_id},
        ).fetchone()
        return int(row[0] or 0)


def get_customer_id_by_name(owner_id: str, name: str) -> int | None:
    """Case-insensitive id lookup. Returns None when the customer does not exist."""
    lower = (name or "").strip().lower()
    if not lower:
        return None
    with get_db() as db:
        row = db.execute(
            text("SELECT id FROM customers WHERE owner_id=:o AND name_lower=:n"),
            {"o": owner_id, "n": lower},
        ).fetchone()
        return int(row[0]) if row else None


def get_balance_by_name(owner_id: str, name: str) -> int:
    """Case-insensitive lookup by name. Returns 0 if customer doesn't exist."""
    customer_id = get_customer_id_by_name(owner_id, name)
    if customer_id is None:
        return 0
    return get_balance_paise(owner_id, customer_id)


def get_last_transaction(owner_id: str) -> dict | None:
    with get_db() as db:
        row = (
            db.query(Transaction)
            .filter(Transaction.owner_id == owner_id, Transaction.is_deleted == 0)
            .order_by(Transaction.id.desc())
            .first()
        )
        if not row:
            return None
        return {
            "id": row.id,
            "customer_id": row.customer_id,
            "customer_name": row.customer_name,
            "amount_paise": row.amount_paise,
            "transaction_type": row.transaction_type,
            "created_at": row.created_at,
        }


def soft_delete_transaction(tx_id: int, owner_id: str) -> bool:
    with get_db() as db:
        row = db.query(Transaction).filter(
            Transaction.id == tx_id,
            Transaction.owner_id == owner_id,
            Transaction.is_deleted == 0,
        ).first()
        if not row:
            return False
        row.is_deleted = 1
        row.deleted_at = utc_now()
        db.commit()
        return True


def update_transaction(
    tx_id: int,
    owner_id: str,
    amount_paise: int | None = None,
    item: str | None = None,
    type_: str | None = None,
) -> bool:
    with get_db() as db:
        row = db.query(Transaction).filter(
            Transaction.id == tx_id,
            Transaction.owner_id == owner_id,
            Transaction.is_deleted == 0,
        ).first()
        if not row:
            return False
        if amount_paise is not None:
            valid, reason = validate_amount_paise(amount_paise)
            if not valid:
                raise ValueError(reason)
            row.amount_paise = amount_paise
        if item is not None:
            row.item_desc = sanitize_input_text(item)
        if type_ is not None:
            if type_ not in VALID_TRANSACTION_TYPES:
                raise ValueError(f"Invalid type_ {type_}")
            row.transaction_type = type_
        db.commit()
        return True


def get_customer_ledger(owner_id: str, customer_id: int, limit: int = 200) -> dict | None:
    with get_db() as db:
        cust = db.query(Customer).filter_by(id=customer_id, owner_id=owner_id).first()
        if not cust:
            return None
        rows = (
            db.query(Transaction)
            .filter(
                Transaction.owner_id == owner_id,
                Transaction.customer_id == customer_id,
                Transaction.is_deleted == 0,
            )
            .order_by(Transaction.date.desc())
            .limit(limit)
            .all()
        )
        entries = [
            {
                "id": t.id,
                "date": t.date.isoformat() if t.date else None,
                "amount_paise": t.amount_paise,
                "transaction_type": t.transaction_type,
                "item_desc": t.item_desc,
            }
            for t in rows
        ]
        return {
            "customer_id": cust.id,
            "customer_name": cust.name,
            "balance_paise": get_balance_paise(owner_id, cust.id),
            "entries": entries,
        }


# ---------------------------------------------------------------------------
# Context persistence
# ---------------------------------------------------------------------------
def save_context(
    sender_id: str,
    customer_name: str | None,
    transaction_type: str = "Debit",
    pending_type: str = "clarification",
    pending_payload: str | None = None,
    customer_id: int | None = None,
):
    with get_db() as db:
        existing = db.query(PendingContext).filter_by(sender_id=sender_id).first()
        if existing:
            existing.customer_name = customer_name
            existing.customer_id = customer_id
            existing.transaction_type = transaction_type
            existing.pending_type = pending_type
            existing.pending_payload = pending_payload
            existing.created_at = utc_now()
        else:
            db.add(
                PendingContext(
                    sender_id=sender_id,
                    customer_name=customer_name,
                    customer_id=customer_id,
                    transaction_type=transaction_type,
                    pending_type=pending_type,
                    pending_payload=pending_payload,
                )
            )
        db.commit()


def consume_context(sender_id: str) -> dict | None:
    """Atomically delete and return a context. Prevents double-consume races."""
    with get_db() as db:
        stmt = (
            delete(PendingContext)
            .where(PendingContext.sender_id == sender_id)
            .returning(
                PendingContext.customer_name,
                PendingContext.customer_id,
                PendingContext.transaction_type,
                PendingContext.pending_type,
                PendingContext.pending_payload,
                PendingContext.created_at,
            )
        )
        row = db.execute(stmt).mappings().first()
        db.commit()

        if not row:
            return None

        age = (utc_now() - row["created_at"]).total_seconds() / 60
        if age > config.CONTEXT_EXPIRY_MINUTES:
            return None

        return {
            "customer_name": row["customer_name"],
            "customer_id": row["customer_id"],
            "transaction_type": row["transaction_type"],
            "pending_type": row["pending_type"] or "clarification",
            "pending_payload": row["pending_payload"],
        }


def peek_context(sender_id: str) -> dict | None:
    """Read context without consuming."""
    with get_db() as db:
        ctx = db.query(PendingContext).filter_by(sender_id=sender_id).first()
        if not ctx:
            return None
        age = (utc_now() - ctx.created_at).total_seconds() / 60
        if age > config.CONTEXT_EXPIRY_MINUTES:
            db.delete(ctx)
            db.commit()
            return None
        return {
            "customer_name": ctx.customer_name,
            "customer_id": ctx.customer_id,
            "transaction_type": ctx.transaction_type,
            "pending_type": ctx.pending_type or "clarification",
            "pending_payload": ctx.pending_payload,
        }


def delete_context(sender_id: str):
    with get_db() as db:
        ctx = db.query(PendingContext).filter_by(sender_id=sender_id).first()
        if ctx:
            db.delete(ctx)
            db.commit()


def cleanup_expired_contexts():
    cutoff = utc_now() - datetime.timedelta(minutes=config.CONTEXT_EXPIRY_MINUTES)
    with get_db() as db:
        n = db.query(PendingContext).filter(PendingContext.created_at < cutoff).delete()
        db.commit()
        if n:
            logger.info(f"Cleaned {n} expired contexts.")


def cleanup_expired_deleted_accounts(retention_days: int = 30) -> int:
    """Permanently purge transactions and records for accounts past the retention window."""
    cutoff = utc_now() - datetime.timedelta(days=retention_days)
    with get_db() as db:
        expired = db.query(AccountDeletion).filter(AccountDeletion.requested_at < cutoff).all()
        if not expired:
            return 0
        owner_ids = [e.owner_id for e in expired]
        tx_count = db.query(Transaction).filter(
            Transaction.owner_id.in_(owner_ids), Transaction.is_deleted == 1
        ).delete(synchronize_session=False)
        db.query(AccountDeletion).filter(AccountDeletion.owner_id.in_(owner_ids)).delete(synchronize_session=False)
        db.commit()
        logger.info(f"Purged {len(owner_ids)} expired account(s) and {tx_count} transaction(s).")
        return len(owner_ids)


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------
def summary_for_period(owner_id: str, start, end) -> dict:
    """Return udhaar/jama/cash totals (paise) for a date range."""
    with get_db() as db:
        row = db.execute(
            text(
                """
            SELECT
              COALESCE(SUM(CASE WHEN transaction_type IN ('Debit','Sale')
                                THEN amount_paise ELSE 0 END), 0),
              COALESCE(SUM(CASE WHEN transaction_type='Credit'
                                THEN amount_paise ELSE 0 END), 0),
              COALESCE(SUM(CASE WHEN transaction_type='CashSale'
                                THEN amount_paise ELSE 0 END), 0)
            FROM transactions
            WHERE owner_id=:o AND is_deleted=0 AND date >= :s AND date < :e
        """
            ),
            {"o": owner_id, "s": start, "e": end},
        ).fetchone()
        return {
            "udhaar_paise": int(row[0] or 0),
            "jama_paise": int(row[1] or 0),
            "cash_sale_paise": int(row[2] or 0),
        }


def health_check() -> bool:
    try:
        with get_db() as db:
            db.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"DB health failed: {e}")
        return False
