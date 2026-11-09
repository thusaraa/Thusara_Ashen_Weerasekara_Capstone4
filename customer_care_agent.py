"""Customer-care agent. Tool calling is a plain loop over the commerce API.

Write tools can only request a confirmation. Approving that confirmation is
human_gate.py, which is not one of the tools bound to the model.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field

from client import CommerceClient
from config import MAX_ITERATIONS, chat_client_kwargs
from memory import CustomerMemory
from orchestration import conditional_route, estimate_confidence, execute_calls
from runtime import ensure_server
from tools import OPENAI_TOOLS, CareTools
import store
from validators import parse_email


SYSTEM = """You are the customer-care agent for Northwind Outfitters.
The signed-in customer is {email}. Use tools for every order, shipping, and refund fact.
Never describe an order that belongs to someone else. Never invent totals, dates, or tracking numbers.

Router hint: confidence {confidence:.2f}, suggested pattern {route}.
The hint is a suggestion. Tool results override it.

How to use the tools:
- A tracking, ETA, or "where is my order" question should call get_order and get_shipping_status together.
- A refund question should call get_order and then check_refund_eligibility.
- Call issue_refund only after eligibility says eligible and the customer asked you to issue, process, or go ahead with the refund.
- Call cancel_order only for an order that has not been delivered, and only when the customer asked to cancel.
- issue_refund and cancel_order do not change the order. They return awaiting_confirmation.
- When a tool returns awaiting_confirmation, tell the customer a supervisor must approve it and include the confirmation id. Do not say the refund or cancellation is done.
- When a tool returns ineligible or error, explain that result and stop.
- When the request is vague and no order id is in the conversation, call escalate_to_human.
- Keep the reply to a short paragraph.
"""


@dataclass
class AgentResult:
    email: str
    message: str
    reply: str
    trace: list[dict]
    stop_reason: str
    dialog: list[dict] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0


def run_turn(
    email: str,
    message: str,
    client: CommerceClient,
    complete,
    memory: CustomerMemory | None = None,
    history: list[dict] | None = None,
) -> AgentResult:
    remembered = memory.recall(email) if memory else "No earlier conversation."
    confidence = estimate_confidence(message, remembered)
    route = conditional_route(message, remembered, confidence)
    messages = [
        {
            "role": "system",
            "content": SYSTEM.format(email=email, confidence=confidence, route=route)
            + "\n\nKnown context:\n"
            + remembered,
        },
        *(history or []),
        {"role": "user", "content": message},
    ]
    tools = CareTools(email, client)
    trace: list[dict] = []
    prompt_tokens = 0
    completion_tokens = 0

    for _ in range(MAX_ITERATIONS):
        step = complete(messages, OPENAI_TOOLS)
        prompt_tokens += int(step.get("prompt_tokens") or 0)
        completion_tokens += int(step.get("completion_tokens") or 0)
        tool_calls = step.get("tool_calls") or []
        messages.append(_assistant_message(step.get("content") or "", tool_calls))
        if not tool_calls:
            reply = (step.get("content") or "").strip()
            if memory:
                memory.save_turn(email, message, reply, trace)
            return AgentResult(
                email=email,
                message=message,
                reply=reply,
                trace=trace,
                stop_reason="goal_met",
                dialog=messages[1:],
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        results = execute_calls(tools, _normalized(tool_calls))
        trace.extend(results)
        for item in results:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item["id"],
                    "content": json.dumps(item["result"]),
                }
            )

    reason = "The agent hit its step limit before it could finish."
    trace.append(tools.run({"id": "budget", "name": "escalate_to_human", "arguments": {"reason": reason}}))
    reply = "I'm handing this to a person because I could not finish inside the step limit."
    if memory:
        memory.save_turn(email, message, reply, trace)
    return AgentResult(
        email=email,
        message=message,
        reply=reply,
        trace=trace,
        stop_reason="max_iterations",
        dialog=messages[1:],
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def build_complete():
    """Chat Completions tool-calling against OpenAI or OpenRouter."""
    from openai import OpenAI

    kwargs = chat_client_kwargs()
    client = OpenAI(api_key=kwargs["api_key"], base_url=kwargs.get("base_url"))
    model = kwargs["model"]

    def complete(messages, tools):
        response = client.chat.completions.create(
            model=model,
            temperature=0,
            messages=messages,
            tools=tools,
        )
        message = response.choices[0].message
        calls = []
        for call in message.tool_calls or []:
            calls.append(
                {
                    "id": call.id,
                    "name": call.function.name,
                    "arguments": call.function.arguments or "{}",
                }
            )
        usage = response.usage
        return {
            "content": message.content or "",
            "tool_calls": calls,
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
        }

    return complete


def _normalized(tool_calls: list[dict]) -> list[dict]:
    normalized = []
    for index, call in enumerate(tool_calls, start=1):
        arguments = call.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                arguments = arguments
        normalized.append(
            {
                "id": call.get("id") or f"call_{index}",
                "name": call.get("name"),
                "arguments": arguments,
            }
        )
    return normalized


def _assistant_message(content: str, tool_calls: list[dict]) -> dict:
    message = {"role": "assistant", "content": content or None}
    if not tool_calls:
        return message
    encoded = []
    for index, call in enumerate(tool_calls, start=1):
        arguments = call.get("arguments", {})
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments)
        encoded.append(
            {
                "id": call.get("id") or f"call_{index}",
                "type": "function",
                "function": {"name": call["name"], "arguments": arguments},
            }
        )
    message["tool_calls"] = encoded
    return message


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the customer-care agent one question")
    parser.add_argument("message")
    parser.add_argument("--customer", default="maya.chen@example.com")
    args = parser.parse_args()
    email = parse_email(args.customer)
    store.init_db()
    client = CommerceClient(base_url=ensure_server())
    result = run_turn(email, args.message, client, build_complete(), CustomerMemory())
    print(result.reply)
    print(f"\nstop: {result.stop_reason}")
    for step in result.trace:
        print(f"- {step['name']}: {json.dumps(step['result'])}")


if __name__ == "__main__":
    main()
