"""Argument checks that run before any order, shipping, or refund call.

The model can still send a bad id, a reason we do not honor, or an amount
outside the allowed range. Those calls stop here, with a message the agent
can repeat to the customer.
"""

from __future__ import annotations

import re

from config import MAX_REFUND_CENTS

ORDER_ID_RE = re.compile(r"^VX-\d{5}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
REASONS = (
    "damaged",
    "wrong_item",
    "not_as_described",
    "changed_mind",
    "late_delivery",
)


class ValidationError(ValueError):
    """The tool arguments themselves are not acceptable."""


def parse_order_id(value) -> str:
    text = str(value or "").strip().upper()
    if not ORDER_ID_RE.fullmatch(text):
        raise ValidationError("order_id must match VX-#####, for example VX-10041")
    return text


def parse_email(value) -> str:
    text = str(value or "").strip().lower()
    if not EMAIL_RE.fullmatch(text):
        raise ValidationError("a customer email is required")
    return text


def parse_reason(value) -> str:
    text = str(value or "").strip().lower()
    if text not in REASONS:
        allowed = ", ".join(REASONS)
        raise ValidationError(f"reason must be one of: {allowed}")
    return text


def parse_amount(value) -> int:
    """Return a positive dollar amount as cents, at most MAX_REFUND_CENTS."""
    if isinstance(value, bool) or value is None or isinstance(value, str) and not value.strip():
        raise ValidationError("amount must be a number of dollars")
    try:
        amount = float(value)
    except (TypeError, ValueError):
        raise ValidationError("amount must be a number of dollars") from None
    cents = int(round(amount * 100))
    if abs(amount * 100 - cents) > 1e-6:
        raise ValidationError("amount can have at most two decimal places")
    if cents <= 0 or cents > MAX_REFUND_CENTS:
        cap = MAX_REFUND_CENTS / 100
        raise ValidationError(f"amount must be greater than 0 and at most {cap:.0f}")
    return cents


def dollars(cents: int) -> float:
    return round(cents / 100, 2)
