"""Confirmation gate for refunds and cancellations.

Read tools never reach this module. A write is stored as `pending` and is
not applied until `approve` runs outside the model's tool list. The commerce
API then demands the token that `approve` created.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from config import DEFECT_REASONS, DEFECT_WINDOW_DAYS, HIGH_VALUE_CENTS, STANDARD_WINDOW_DAYS
from store import (
    StoreError,
    create_approval,
    execute_cancel,
    execute_refund,
    find_open_approval,
    get_approval,
    mark_approved,
    owned_order,
)
from validators import dollars


def evaluate(order: dict, reason: str, amount_cents: int, today: date | None = None) -> dict:
    """Say whether this refund would be allowed. Does not change the order."""
    today = today or datetime.now(timezone.utc).date()
    window = DEFECT_WINDOW_DAYS if reason in DEFECT_REASONS else STANDARD_WINDOW_DAYS
    refundable_cents = int(order.get("refundable_cents", round(order["refundable"] * 100)))
    high_value = amount_cents >= HIGH_VALUE_CENTS
    base = {
        "eligible": False,
        "order_id": order["order_id"],
        "order_status": order["status"],
        "reason": reason,
        "amount": dollars(amount_cents),
        "refundable_balance": order["refundable"],
        "window_days": window,
        "days_since_delivery": None,
        "high_value": high_value,
    }

    if order["status"] == "cancelled":
        return {**base, "explanation": "Cancelled orders cannot be refunded."}
    if order["status"] != "delivered":
        return {
            **base,
            "explanation": (
                f"Refunds start after delivery. This order is {order['status']}."
            ),
        }
    if refundable_cents <= 0:
        return {**base, "explanation": "This order is already fully refunded."}
    if amount_cents > refundable_cents:
        return {
            **base,
            "explanation": (
                f"Amount exceeds the refundable balance of ${order['refundable']:.2f}."
            ),
        }

    delivered = date.fromisoformat(order["delivered_on"])
    days = (today - delivered).days
    base["days_since_delivery"] = days
    if days > window:
        return {
            **base,
            "explanation": (
                f"{reason} refunds must be requested within {window} days of delivery. "
                f"This order was delivered {days} days ago."
            ),
        }
    return {
        **base,
        "eligible": True,
        "explanation": (
            f"Eligible for a {reason} refund of ${dollars(amount_cents):.2f}. "
            "A supervisor still has to approve the write."
        ),
    }


def request_refund(email: str, order_id: str, reason: str, amount_cents: int) -> dict:
    order = owned_order(email, order_id)
    decision = evaluate(order, reason, amount_cents)
    if not decision["eligible"]:
        decision["status"] = "ineligible"
        return decision

    existing = find_open_approval("issue_refund", order_id, reason, amount_cents)
    if existing and existing["status"] == "executed":
        return {
            "status": "executed",
            "confirmation_id": existing["confirmation_id"],
            "order_id": order_id,
            "amount": dollars(amount_cents),
            "reason": reason,
            "high_value": existing["high_value"],
            "message": "This refund was already issued.",
        }
    if existing and existing["status"] in {"pending", "approved"}:
        return _pending(existing)

    created = create_approval(
        {
            "action": "issue_refund",
            "order_id": order_id,
            "email": email,
            "reason": reason,
            "amount_cents": amount_cents,
            "high_value": decision["high_value"],
        }
    )
    return _pending(created)


def request_cancel(email: str, order_id: str, note: str) -> dict:
    order = owned_order(email, order_id)
    if order["status"] == "cancelled":
        return {
            "status": "executed",
            "order_id": order_id,
            "message": "This order is already cancelled.",
        }
    if order["status"] not in {"placed", "in_transit"}:
        return {
            "status": "ineligible",
            "order_id": order_id,
            "order_status": order["status"],
            "explanation": (
                f"Orders with status {order['status']} cannot be cancelled. "
                "A delivered order can be reviewed for a refund instead."
            ),
        }
    existing = find_open_approval("cancel_order", order_id, None, None)
    if existing and existing["status"] in {"pending", "approved", "executed"}:
        if existing["status"] == "executed":
            return {
                "status": "executed",
                "confirmation_id": existing["confirmation_id"],
                "order_id": order_id,
                "message": "This cancellation was already completed.",
            }
        return _pending(existing)
    created = create_approval(
        {
            "action": "cancel_order",
            "order_id": order_id,
            "email": email,
            "reason": None,
            "amount_cents": None,
            "note": note[:240],
            "high_value": False,
        }
    )
    return _pending(created)


def approve(confirmation_id: str, actor: str = "supervisor") -> dict:
    """Human step. Not a model tool. Returns a one-time token for the API."""
    existing = get_approval(confirmation_id, include_token=True)
    if existing is None:
        raise StoreError("Unknown confirmation.", 404)
    if existing["status"] == "rejected":
        raise StoreError("Confirmation was rejected.", 409)
    if existing["status"] == "pending":
        return mark_approved(confirmation_id, actor)
    return existing


def apply_refund(email: str, confirmation_id: str, token: str) -> dict:
    return execute_refund(email, confirmation_id, token)


def apply_cancel(email: str, confirmation_id: str, token: str) -> dict:
    return execute_cancel(email, confirmation_id, token)


def _pending(approval: dict) -> dict:
    amount = approval.get("amount")
    message = "Waiting for a supervisor. Nothing has been changed on the order."
    if approval.get("high_value"):
        message = (
            "Waiting for a supervisor. This refund is over $200, so it is flagged "
            "high value. Nothing has been changed on the order."
        )
    return {
        "status": "awaiting_confirmation",
        "confirmation_id": approval["confirmation_id"],
        "action": approval["action"],
        "order_id": approval["order_id"],
        "amount": amount,
        "reason": approval.get("reason"),
        "high_value": approval.get("high_value", False),
        "message": message,
    }
