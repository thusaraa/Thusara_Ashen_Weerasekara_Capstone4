"""SQLite orders, shipments, refunds, approvals, and the audit log.

Money is stored in cents. Callers above this module speak in dollars.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from config import db_path
from validators import dollars

_lock = threading.RLock()


class StoreError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.message = message
        self.status = status


def now_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def transaction():
    db_path().parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        conn = sqlite3.connect(db_path(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db() -> None:
    with transaction() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                customer_name TEXT NOT NULL,
                status TEXT NOT NULL,
                total_cents INTEGER NOT NULL,
                placed_on TEXT NOT NULL,
                delivered_on TEXT,
                items_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shipments (
                order_id TEXT PRIMARY KEY,
                carrier TEXT NOT NULL,
                tracking_number TEXT NOT NULL,
                status TEXT NOT NULL,
                eta TEXT,
                last_scan TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS refunds (
                refund_id INTEGER PRIMARY KEY AUTOINCREMENT,
                confirmation_id TEXT UNIQUE,
                order_id TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS approvals (
                confirmation_id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                order_id TEXT NOT NULL,
                email TEXT NOT NULL,
                reason TEXT,
                amount_cents INTEGER,
                note TEXT,
                status TEXT NOT NULL,
                token TEXT,
                high_value INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                decided_at TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                email TEXT NOT NULL,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                order_id TEXT,
                decision TEXT NOT NULL,
                detail TEXT NOT NULL
            );
            """
        )
        count = conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
        if count == 0:
            _seed(conn)


def reset() -> None:
    path = db_path()
    if path.exists():
        path.unlink()
    init_db()


def _seed(conn: sqlite3.Connection) -> None:
    today = datetime.now(timezone.utc).date()

    def day(offset: int) -> str:
        return (today + timedelta(days=offset)).isoformat()

    orders = [
        (
            "VX-10041", "maya.chen@example.com", "Maya Chen", "delivered", 8900,
            day(-14), day(-12),
            [{"sku": "HP-100", "name": "TrailBuds Headphones", "qty": 1, "price": 89.00}],
        ),
        (
            "VX-10042", "maya.chen@example.com", "Maya Chen", "in_transit", 6400,
            day(-2), None,
            [{"sku": "CB-200", "name": "USB-C Cable 2-pack", "qty": 1, "price": 64.00}],
        ),
        (
            "VX-10043", "jonah.patel@example.com", "Jonah Patel", "delivered", 4500,
            day(-55), day(-50),
            [{"sku": "SH-310", "name": "Linen Shirt", "qty": 1, "price": 45.00}],
        ),
        (
            "VX-10044", "jonah.patel@example.com", "Jonah Patel", "delivered", 32000,
            day(-9), day(-6),
            [{"sku": "CM-440", "name": "Mirrorless Camera", "qty": 1, "price": 320.00}],
        ),
        (
            "VX-10045", "amina.yusuf@example.com", "Amina Yusuf", "delivered", 6000,
            day(-12), day(-8),
            [{"sku": "BN-150", "name": "Wool Beanie", "qty": 1, "price": 60.00}],
        ),
        (
            "VX-10046", "amina.yusuf@example.com", "Amina Yusuf", "cancelled", 11000,
            day(-1), None,
            [{"sku": "BG-220", "name": "Weekender Bag", "qty": 1, "price": 110.00}],
        ),
    ]
    for order_id, email, name, status, total, placed, delivered, items in orders:
        conn.execute(
            """
            INSERT INTO orders
                (order_id, email, customer_name, status, total_cents, placed_on, delivered_on, items_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (order_id, email, name, status, total, placed, delivered, json.dumps(items)),
        )

    shipments = [
        ("VX-10041", "UPS", "1ZDEMO10041", "delivered", day(-12), "Left at the front door"),
        ("VX-10042", "UPS", "1ZDEMO10042", "in_transit", day(4), "Memphis, TN"),
        ("VX-10043", "USPS", "9400DEMO10043", "delivered", day(-50), "Delivered to mailbox"),
        ("VX-10044", "FedEx", "FDXDEMO10044", "delivered", day(-6), "Signed for by J. Patel"),
        ("VX-10045", "USPS", "9400DEMO10045", "delivered", day(-8), "Delivered to mailbox"),
        ("VX-10046", "UPS", "1ZDEMO10046", "cancelled", None, "Label voided before pickup"),
    ]
    conn.executemany(
        """
        INSERT INTO shipments (order_id, carrier, tracking_number, status, eta, last_scan)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        shipments,
    )
    conn.execute(
        """
        INSERT INTO refunds (confirmation_id, order_id, amount_cents, reason, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("conf_seeded45", "VX-10045", 6000, "changed_mind", now_stamp()),
    )


def _refunded_cents(conn: sqlite3.Connection, order_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) AS total FROM refunds WHERE order_id = ?",
        (order_id,),
    ).fetchone()
    return int(row["total"])


def _public_order(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    refunded = _refunded_cents(conn, row["order_id"])
    total = int(row["total_cents"])
    return {
        "order_id": row["order_id"],
        "email": row["email"],
        "customer_name": row["customer_name"],
        "status": row["status"],
        "total": dollars(total),
        "refunded": dollars(refunded),
        "refundable": dollars(max(total - refunded, 0)),
        "total_cents": total,
        "refunded_cents": refunded,
        "refundable_cents": max(total - refunded, 0),
        "placed_on": row["placed_on"],
        "delivered_on": row["delivered_on"],
        "items": json.loads(row["items_json"]),
    }


def get_order(order_id: str) -> dict | None:
    with transaction() as conn:
        row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        if row is None:
            return None
        return _public_order(conn, row)


def get_shipping(order_id: str) -> dict | None:
    with transaction() as conn:
        row = conn.execute("SELECT * FROM shipments WHERE order_id = ?", (order_id,)).fetchone()
        if row is None:
            return None
        return {
            "order_id": row["order_id"],
            "carrier": row["carrier"],
            "tracking_number": row["tracking_number"],
            "status": row["status"],
            "eta": row["eta"],
            "last_scan": row["last_scan"],
        }


def owned_order(email: str, order_id: str) -> dict:
    order = get_order(order_id)
    if order is None or order["email"] != email:
        raise StoreError("Order not found on this account.", 404)
    return order


def find_open_approval(action: str, order_id: str, reason: str | None, amount_cents: int | None) -> dict | None:
    with transaction() as conn:
        row = conn.execute(
            """
            SELECT * FROM approvals
            WHERE action = ? AND order_id = ? AND IFNULL(reason, '') = IFNULL(?, '')
              AND IFNULL(amount_cents, -1) = IFNULL(?, -1)
              AND status IN ('pending', 'approved', 'executed')
            ORDER BY created_at DESC LIMIT 1
            """,
            (action, order_id, reason, amount_cents),
        ).fetchone()
        return _public_approval(row, include_token=True) if row else None


def create_approval(record: dict) -> dict:
    confirmation_id = "conf_" + secrets.token_hex(4)
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO approvals (
                confirmation_id, action, order_id, email, reason, amount_cents, note,
                status, token, high_value, created_at, decided_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, ?, NULL)
            """,
            (
                confirmation_id,
                record["action"],
                record["order_id"],
                record["email"],
                record.get("reason"),
                record.get("amount_cents"),
                record.get("note"),
                1 if record.get("high_value") else 0,
                now_stamp(),
            ),
        )
        _audit(
            conn,
            email=record["email"],
            actor="agent",
            action=record["action"],
            order_id=record["order_id"],
            decision="requested",
            detail={
                "confirmation_id": confirmation_id,
                "reason": record.get("reason"),
                "amount": dollars(record["amount_cents"]) if record.get("amount_cents") is not None else None,
                "high_value": bool(record.get("high_value")),
            },
        )
        row = conn.execute(
            "SELECT * FROM approvals WHERE confirmation_id = ?", (confirmation_id,)
        ).fetchone()
        return _public_approval(row)


def get_approval(confirmation_id: str, include_token: bool = False) -> dict | None:
    with transaction() as conn:
        row = conn.execute(
            "SELECT * FROM approvals WHERE confirmation_id = ?", (confirmation_id,)
        ).fetchone()
        return _public_approval(row, include_token=include_token) if row else None


def mark_approved(confirmation_id: str, actor: str) -> dict:
    token = secrets.token_urlsafe(18)
    with transaction() as conn:
        row = conn.execute(
            "SELECT * FROM approvals WHERE confirmation_id = ?", (confirmation_id,)
        ).fetchone()
        if row is None:
            raise StoreError("Unknown confirmation.", 404)
        if row["status"] == "pending":
            conn.execute(
                """
                UPDATE approvals
                SET status = 'approved', token = ?, decided_at = ?
                WHERE confirmation_id = ? AND status = 'pending'
                """,
                (token, now_stamp(), confirmation_id),
            )
            _audit(
                conn,
                email=row["email"],
                actor=actor,
                action=row["action"],
                order_id=row["order_id"],
                decision="approved",
                detail={"confirmation_id": confirmation_id, "high_value": bool(row["high_value"])},
            )
        updated = conn.execute(
            "SELECT * FROM approvals WHERE confirmation_id = ?", (confirmation_id,)
        ).fetchone()
        return _public_approval(updated, include_token=True)


def execute_refund(email: str, confirmation_id: str, token: str) -> dict:
    rejection = None
    with transaction() as conn:
        approval = _locked_approval(conn, confirmation_id)
        _check_token(approval, token, "issue_refund", email)
        existing = conn.execute(
            "SELECT * FROM refunds WHERE confirmation_id = ?", (confirmation_id,)
        ).fetchone()
        if approval["status"] == "executed" and existing is not None:
            return _receipt(existing, approval)

        order = conn.execute(
            "SELECT * FROM orders WHERE order_id = ?", (approval["order_id"],)
        ).fetchone()
        public = _public_order(conn, order)
        from gate import evaluate

        decision = evaluate(public, approval["reason"], int(approval["amount_cents"]))
        if not decision["eligible"]:
            rejection = decision["explanation"]
            conn.execute(
                "UPDATE approvals SET status = 'rejected', decided_at = ? WHERE confirmation_id = ?",
                (now_stamp(), confirmation_id),
            )
            _audit(
                conn,
                email=email,
                actor="system",
                action="issue_refund",
                order_id=approval["order_id"],
                decision="rejected",
                detail={"confirmation_id": confirmation_id, "explanation": rejection},
            )
        else:
            conn.execute(
                """
                INSERT INTO refunds (confirmation_id, order_id, amount_cents, reason, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    confirmation_id,
                    approval["order_id"],
                    int(approval["amount_cents"]),
                    approval["reason"],
                    now_stamp(),
                ),
            )
            conn.execute(
                "UPDATE approvals SET status = 'executed', decided_at = ? WHERE confirmation_id = ?",
                (now_stamp(), confirmation_id),
            )
            _audit(
                conn,
                email=email,
                actor="system",
                action="issue_refund",
                order_id=approval["order_id"],
                decision="executed",
                detail={
                    "confirmation_id": confirmation_id,
                    "amount": dollars(int(approval["amount_cents"])),
                    "reason": approval["reason"],
                },
            )
            existing = conn.execute(
                "SELECT * FROM refunds WHERE confirmation_id = ?", (confirmation_id,)
            ).fetchone()
            receipt = _receipt(existing, approval)
    if rejection:
        raise StoreError(rejection, 409)
    return receipt


def execute_cancel(email: str, confirmation_id: str, token: str) -> dict:
    rejection = None
    with transaction() as conn:
        approval = _locked_approval(conn, confirmation_id)
        _check_token(approval, token, "cancel_order", email)
        order = conn.execute(
            "SELECT * FROM orders WHERE order_id = ?", (approval["order_id"],)
        ).fetchone()
        if approval["status"] == "executed" or order["status"] == "cancelled":
            conn.execute(
                "UPDATE approvals SET status = 'executed' WHERE confirmation_id = ? AND status != 'executed'",
                (confirmation_id,),
            )
            receipt = {
                "status": "executed",
                "action": "cancel_order",
                "confirmation_id": confirmation_id,
                "order_id": approval["order_id"],
                "order_status": "cancelled",
            }
        elif order["status"] not in {"placed", "in_transit"}:
            rejection = f"Orders with status {order['status']} cannot be cancelled."
            conn.execute(
                "UPDATE approvals SET status = 'rejected', decided_at = ? WHERE confirmation_id = ?",
                (now_stamp(), confirmation_id),
            )
            _audit(
                conn,
                email=email,
                actor="system",
                action="cancel_order",
                order_id=approval["order_id"],
                decision="rejected",
                detail={"confirmation_id": confirmation_id, "explanation": rejection},
            )
            receipt = None
        else:
            conn.execute(
                "UPDATE orders SET status = 'cancelled' WHERE order_id = ?", (approval["order_id"],)
            )
            conn.execute(
                "UPDATE shipments SET status = 'cancelled', eta = NULL, last_scan = ? WHERE order_id = ?",
                ("Cancelled before delivery", approval["order_id"]),
            )
            conn.execute(
                "UPDATE approvals SET status = 'executed', decided_at = ? WHERE confirmation_id = ?",
                (now_stamp(), confirmation_id),
            )
            _audit(
                conn,
                email=email,
                actor="system",
                action="cancel_order",
                order_id=approval["order_id"],
                decision="executed",
                detail={"confirmation_id": confirmation_id},
            )
            receipt = {
                "status": "executed",
                "action": "cancel_order",
                "confirmation_id": confirmation_id,
                "order_id": approval["order_id"],
                "order_status": "cancelled",
            }
    if rejection:
        raise StoreError(rejection, 409)
    return receipt


def add_audit(email: str, actor: str, action: str, decision: str, detail: dict, order_id: str | None = None) -> None:
    with transaction() as conn:
        _audit(conn, email, actor, action, order_id, decision, detail)


def list_audit(email: str) -> list[dict]:
    with transaction() as conn:
        rows = conn.execute(
            """
            SELECT * FROM audit_log WHERE email = ? ORDER BY id
            """,
            (email,),
        ).fetchall()
        return [_public_audit(row) for row in rows]


def _locked_approval(conn: sqlite3.Connection, confirmation_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM approvals WHERE confirmation_id = ?", (confirmation_id,)
    ).fetchone()
    if row is None:
        raise StoreError("Unknown confirmation.", 404)
    return row


def _check_token(approval: sqlite3.Row, token: str, action: str, email: str) -> None:
    if approval["action"] != action or approval["email"] != email:
        raise StoreError("This confirmation does not match the requested write.", 403)
    if approval["status"] not in {"approved", "executed"} or not approval["token"] or approval["token"] != token:
        raise StoreError("Write blocked. A supervisor confirmation token is required.", 403)


def _audit(conn, email, actor, action, order_id, decision, detail) -> None:
    conn.execute(
        """
        INSERT INTO audit_log (created_at, email, actor, action, order_id, decision, detail)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (now_stamp(), email, actor, action, order_id, decision, json.dumps(detail)),
    )


def _public_approval(row: sqlite3.Row, include_token: bool = False) -> dict:
    amount = row["amount_cents"]
    public = {
        "confirmation_id": row["confirmation_id"],
        "action": row["action"],
        "order_id": row["order_id"],
        "email": row["email"],
        "reason": row["reason"],
        "amount": dollars(amount) if amount is not None else None,
        "note": row["note"],
        "status": row["status"],
        "high_value": bool(row["high_value"]),
        "created_at": row["created_at"],
    }
    if include_token:
        public["token"] = row["token"]
    return public


def _public_audit(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "email": row["email"],
        "actor": row["actor"],
        "action": row["action"],
        "order_id": row["order_id"],
        "decision": row["decision"],
        "detail": json.loads(row["detail"]),
    }


def _receipt(refund: sqlite3.Row, approval: sqlite3.Row) -> dict:
    return {
        "status": "executed",
        "action": "issue_refund",
        "refund_id": refund["refund_id"],
        "confirmation_id": refund["confirmation_id"],
        "order_id": refund["order_id"],
        "amount": dollars(int(refund["amount_cents"])),
        "reason": refund["reason"],
        "high_value": bool(approval["high_value"]),
    }
