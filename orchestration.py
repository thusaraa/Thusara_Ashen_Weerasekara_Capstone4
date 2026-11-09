"""Sequential, parallel, and conditional tool patterns.

The model chooses tools itself. These functions make the three patterns
explicit and testable: one step after another, two reads at the same time,
or a route chosen from the message before any tool runs.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from validators import ORDER_ID_RE


def execute_calls(tools, calls: list[dict]) -> list[dict]:
    if not calls:
        return []
    if len(calls) == 1:
        return [tools.run(calls[0])]
    with ThreadPoolExecutor(max_workers=min(4, len(calls))) as pool:
        futures = [pool.submit(tools.run, call) for call in calls]
        return [future.result() for future in futures]


def parallel_lookup(tools, order_id: str) -> dict:
    steps = execute_calls(
        tools,
        [
            {"id": "order", "name": "get_order", "arguments": {"order_id": order_id}},
            {"id": "shipping", "name": "get_shipping_status", "arguments": {"order_id": order_id}},
        ],
    )
    return {"pattern": "parallel", "steps": steps}


def sequential_refund(tools, order_id: str, reason: str, amount: float) -> dict:
    order_step = tools.run(
        {"id": "order", "name": "get_order", "arguments": {"order_id": order_id}}
    )
    result = order_step["result"]
    if result.get("error") or result.get("status") != "delivered":
        return {"pattern": "sequential", "stopped_after": "get_order", "steps": [order_step]}
    eligibility = tools.run(
        {
            "id": "eligibility",
            "name": "check_refund_eligibility",
            "arguments": {"order_id": order_id, "reason": reason, "amount": amount},
        }
    )
    return {
        "pattern": "sequential",
        "stopped_after": "check_refund_eligibility",
        "steps": [order_step, eligibility],
    }


def estimate_confidence(message: str, memory_text: str = "") -> float:
    text = message.lower()
    has_order = bool(ORDER_ID_RE.search(message)) or bool(ORDER_ID_RE.search(memory_text))
    vague = ("make it right", "you know what i mean", "just fix it", "do whatever", "it's all wrong")
    if any(phrase in text for phrase in vague) and not has_order:
        return 0.25
    if has_order:
        return 0.9
    if any(word in text for word in ("order", "refund", "package", "shipping")):
        return 0.55
    return 0.35


def conditional_route(message: str, memory_text: str = "", confidence: float | None = None) -> str:
    score = estimate_confidence(message, memory_text) if confidence is None else confidence
    if score < 0.4:
        return "escalate_to_human"
    text = message.lower()
    if any(word in text for word in ("refund", "money back", "return")):
        return "sequential_refund"
    if any(word in text for word in ("ship", "track", "tracking", "eta", "arrive", "package", "where is", "delivery")):
        return "parallel_lookup"
    return "order_lookup"
