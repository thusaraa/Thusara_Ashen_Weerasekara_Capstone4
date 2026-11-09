"""Five customer scenarios, plus the supervisor approval the agent cannot perform.

The pattern section calls the API directly, so the refund gate is visible
even when no model key is set. When a key is set, the same scenarios also
run through the agent and demo_run.md includes that transcript.
"""

from __future__ import annotations

import json
from pathlib import Path

from client import CommerceClient
from customer_care_agent import build_complete, run_turn
from human_gate import approve_and_execute
from memory import CustomerMemory
from orchestration import conditional_route, parallel_lookup, sequential_refund
from runtime import ensure_server
from tools import CareTools
import store

ROOT = Path(__file__).resolve().parent
MAYA = "maya.chen@example.com"
JONAH = "jonah.patel@example.com"

SCENARIOS = [
    {
        "title": "1. Order lookup",
        "email": MAYA,
        "message": "Look up order VX-10041 and tell me what I bought and whether it was delivered.",
    },
    {
        "title": "2. Shipping ETA",
        "email": MAYA,
        "message": "Where is order VX-10042? I need the tracking number and the ETA.",
    },
    {
        "title": "3. Eligible refund check",
        "email": MAYA,
        "message": "Is a damaged refund of $89.00 eligible for order VX-10041? Do not issue it yet.",
    },
    {
        "title": "4. Ineligible refund",
        "email": JONAH,
        "message": "I changed my mind about order VX-10043. Can I get $45.00 back?",
    },
    {
        "title": "5. Refund with approval",
        "email": JONAH,
        "message": "Please issue a damaged refund of $320.00 for order VX-10044.",
    },
]


def main() -> None:
    store.reset()
    base = ensure_server()
    client = CommerceClient(base_url=base)
    memory = CustomerMemory()
    for email in (MAYA, JONAH):
        memory.clear_customer(email)

    sections = [
        "# Capstone 4 demo — customer care",
        "",
        "Read tools answer on their own. `issue_refund` only creates a confirmation. A supervisor approves it, and the API then writes the refund.",
        "",
        "## Patterns",
        "",
        _patterns(client),
        "",
        "## Five scenarios",
        "",
    ]
    agent_note, transcripts = _agent_scenarios(client, memory)
    sections.extend([agent_note, ""])
    sections.extend(transcripts)
    sections.extend(["", "## Governed refund", "", _governed_refund(client), ""])
    sections.extend(["## Audit", ""])
    for email in (MAYA, JONAH):
        sections.append(f"### {email}")
        sections.append("")
        sections.append(_audit_block(client, email))
        sections.append("")

    path = ROOT / "demo_run.md"
    path.write_text("\n".join(sections).rstrip() + "\n", encoding="utf-8")
    print(f"Wrote {path}")


def _patterns(client: CommerceClient) -> str:
    maya = CareTools(MAYA, client)
    jonah = CareTools(JONAH, client)
    parallel = parallel_lookup(maya, "VX-10042")
    eligible = sequential_refund(maya, "VX-10041", "damaged", 89)
    stopped = sequential_refund(maya, "VX-10042", "changed_mind", 64)
    denied = sequential_refund(jonah, "VX-10043", "changed_mind", 45)
    routes = [
        ("Look up order VX-10041.", conditional_route("Look up order VX-10041.")),
        ("Where is order VX-10042?", conditional_route("Where is order VX-10042?")),
        ("Please refund order VX-10044.", conditional_route("Please refund order VX-10044.")),
        ("just fix it", conditional_route("just fix it")),
    ]
    lines = [
        "Parallel lookup of VX-10042 returned "
        + ", ".join(step["name"] for step in parallel["steps"])
        + f". Tracking {parallel['steps'][1]['result'].get('tracking_number')}, "
        + f"status {parallel['steps'][1]['result'].get('status')}.",
        "",
        "Sequential refund check for VX-10041 stopped after "
        + eligible["stopped_after"]
        + f". Eligible: {eligible['steps'][-1]['result'].get('eligible')}.",
        "",
        "Sequential refund check for in-transit VX-10042 stopped after "
        + stopped["stopped_after"]
        + ". Eligibility was not called.",
        "",
        "Sequential check for VX-10043 changed_mind: "
        + str(denied["steps"][-1]["result"].get("explanation")),
        "",
        "Conditional routes:",
    ]
    for message, route in routes:
        lines.append(f"- `{message}` -> {route}")
    return "\n".join(lines)


def _agent_scenarios(client: CommerceClient, memory: CustomerMemory) -> tuple[str, list[str]]:
    try:
        complete = build_complete()
    except RuntimeError as exc:
        return f"Agent transcripts were not run: {exc}", []

    blocks = []
    for scenario in SCENARIOS:
        print(scenario["title"])
        try:
            result = run_turn(
                scenario["email"],
                scenario["message"],
                client,
                complete,
                memory,
            )
        except Exception as exc:
            blocks.extend(
                [
                    f"### {scenario['title']}",
                    "",
                    f"Customer ({scenario['email']}): {scenario['message']}",
                    "",
                    f"The agent call failed: {exc}",
                    "",
                ]
            )
            continue
        blocks.extend(_transcript(scenario, result))
        if scenario["title"].startswith("5."):
            pending = _pending_id(result.trace)
            if pending:
                receipt = approve_and_execute(client, scenario["email"], pending)
                follow = (
                    "Supervisor approved "
                    + pending
                    + ". Receipt: "
                    + json.dumps(receipt)
                    + ". Confirm this refund to the customer in one short paragraph. Do not request another refund."
                )
                try:
                    second = run_turn(
                        scenario["email"],
                        follow,
                        client,
                        complete,
                        memory,
                        history=result.dialog,
                    )
                    spoken = second.reply
                except Exception as exc:
                    spoken = f"Refund issued. Receipt {json.dumps(receipt)}. Follow-up call failed: {exc}"
                blocks.extend(["Supervisor receipt:", "", "```json", json.dumps(receipt, indent=2), "```", "", spoken, ""])
    return "The agent used the tool loop below. Scenario 5 is approved only in the supervisor step.", blocks


def _governed_refund(client: CommerceClient) -> str:
    """Run the high-value write if the agent did not already complete it."""
    order = client.get_order(JONAH, "VX-10044")
    if order.get("refunded"):
        return (
            "VX-10044 was refunded through the agent confirmation above. "
            f"Refunded amount: ${order['refunded']:.2f}. The order total was ${order['total']:.2f}."
        )
    requested = client.request_refund(JONAH, "VX-10044", "damaged", 320)
    if requested.get("status") != "awaiting_confirmation":
        return "The governed refund did not open a confirmation:\n\n```json\n" + json.dumps(requested, indent=2) + "\n```"
    before = client.get_order(JONAH, "VX-10044")
    receipt = approve_and_execute(client, JONAH, requested["confirmation_id"])
    after = client.get_order(JONAH, "VX-10044")
    return "\n".join(
        [
            "The agent did not leave a pending confirmation, so the supervisor path was run directly.",
            "",
            f"Before approval, refunded ${before['refunded']:.2f}.",
            f"Confirmation `{requested['confirmation_id']}` (high value: {requested['high_value']}).",
            "After approval:",
            "",
            "```json",
            json.dumps(receipt, indent=2),
            "```",
            "",
            f"Order refunded amount is now ${after['refunded']:.2f}.",
        ]
    )


def _transcript(scenario: dict, result) -> list[str]:
    lines = [
        f"### {scenario['title']}",
        "",
        f"Customer ({scenario['email']}): {scenario['message']}",
        "",
        f"Stop: {result.stop_reason}. Tokens in {result.prompt_tokens}, out {result.completion_tokens}.",
        "",
    ]
    if result.trace:
        lines.append("Tools:")
        lines.append("")
        for step in result.trace:
            lines.append(f"- `{step['name']}` {json.dumps(step['arguments'])}")
            lines.append(f"  {json.dumps(step['result'])}")
        lines.append("")
    lines.extend([result.reply or "(no reply)", ""])
    return lines


def _pending_id(trace: list[dict]) -> str:
    for step in trace:
        result = step.get("result") or {}
        if result.get("status") == "awaiting_confirmation":
            return result["confirmation_id"]
    return ""


def _audit_block(client: CommerceClient, email: str) -> str:
    events = client.audit(email)
    if not events:
        return "No write or escalation events."
    lines = []
    for event in events:
        lines.append(
            f"- {event['decision']} {event['action']} on {event.get('order_id') or 'no order'} "
            f"by {event['actor']}: {json.dumps(event['detail'])}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
