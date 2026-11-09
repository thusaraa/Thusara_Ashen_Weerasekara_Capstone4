"""Tool schemas and the dispatch loop the model is allowed to call.

issue_refund and cancel_order create a pending confirmation. They do not
receive a token, and they are not able to approve their own request.
"""

from __future__ import annotations

import json

from client import CommerceClient
from store import StoreError, add_audit
from validators import REASONS, ValidationError, dollars, parse_amount, parse_order_id, parse_reason

OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_order",
            "description": "Read one order on the signed-in customer's account.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "pattern": "^VX-\\d{5}$"},
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_shipping_status",
            "description": "Read the carrier, tracking number, status, and ETA for one order.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "pattern": "^VX-\\d{5}$"},
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_refund_eligibility",
            "description": "Check whether a refund would be allowed. This does not move money.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "pattern": "^VX-\\d{5}$"},
                    "reason": {"type": "string", "enum": list(REASONS)},
                    "amount": {"type": "number", "minimum": 0.01, "maximum": 500},
                },
                "required": ["order_id", "reason", "amount"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "issue_refund",
            "description": (
                "Ask a supervisor to refund an eligible order. "
                "The order is unchanged until a person approves the confirmation id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "pattern": "^VX-\\d{5}$"},
                    "reason": {"type": "string", "enum": list(REASONS)},
                    "amount": {"type": "number", "minimum": 0.01, "maximum": 500},
                },
                "required": ["order_id", "reason", "amount"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_order",
            "description": (
                "Ask a supervisor to cancel an order that has not been delivered. "
                "Delivered orders cannot be cancelled."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "pattern": "^VX-\\d{5}$"},
                    "note": {"type": "string"},
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": "Hand the conversation to a person when the request is ambiguous or out of policy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "order_id": {"type": "string"},
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    },
]


def _public(value):
    if isinstance(value, dict):
        return {key: _public(item) for key, item in value.items() if key != "token"}
    if isinstance(value, list):
        return [_public(item) for item in value]
    return value


class CareTools:
    def __init__(self, email: str, client: CommerceClient):
        self.email = email
        self.client = client

    def run(self, call: dict) -> dict:
        name = call.get("name") or ""
        raw_args = call.get("arguments") if call.get("arguments") is not None else {}
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args or "{}")
            except json.JSONDecodeError:
                raw_args = None
        if not isinstance(raw_args, dict):
            result = {"error": "arguments were not a JSON object"}
        else:
            try:
                result = self._dispatch(name, raw_args)
            except ValidationError as exc:
                result = {"error": str(exc)}
            except StoreError as exc:
                result = {"error": exc.message}
        return {
            "id": call.get("id"),
            "name": name,
            "arguments": raw_args if isinstance(raw_args, dict) else {},
            "result": _public(result),
        }

    def _dispatch(self, name: str, args: dict) -> dict:
        if name == "get_order":
            return self.client.get_order(self.email, parse_order_id(args.get("order_id")))
        if name == "get_shipping_status":
            return self.client.get_shipping(self.email, parse_order_id(args.get("order_id")))
        if name == "check_refund_eligibility":
            order_id = parse_order_id(args.get("order_id"))
            reason = parse_reason(args.get("reason"))
            cents = parse_amount(args.get("amount"))
            return self.client.eligibility(self.email, order_id, reason, dollars(cents))
        if name == "issue_refund":
            order_id = parse_order_id(args.get("order_id"))
            reason = parse_reason(args.get("reason"))
            cents = parse_amount(args.get("amount"))
            return self.client.request_refund(self.email, order_id, reason, dollars(cents))
        if name == "cancel_order":
            order_id = parse_order_id(args.get("order_id"))
            note = str(args.get("note") or "").strip()
            return self.client.request_cancel(self.email, order_id, note)
        if name == "escalate_to_human":
            reason = str(args.get("reason") or "").strip()
            if not reason:
                raise ValidationError("reason is required")
            order_id = parse_order_id(args["order_id"]) if args.get("order_id") else None
            add_audit(
                self.email,
                actor="agent",
                action="escalate_to_human",
                decision="escalated",
                order_id=order_id,
                detail={"reason": reason[:500]},
            )
            return {"status": "escalated", "reason": reason, "order_id": order_id}
        raise ValidationError(f"unknown tool {name}")
