"""Checks that do not call a model."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from datetime import date, timedelta

from fastapi.testclient import TestClient

from client import from_test_client
from customer_care_agent import run_turn
from gate import evaluate
from human_gate import approve_and_execute
from memory import CustomerMemory
from orchestration import conditional_route, execute_calls, parallel_lookup, sequential_refund
from tools import CareTools
from validators import ValidationError, parse_amount, parse_order_id, parse_reason


class ValidatorTests(unittest.TestCase):
    def test_order_id_shape(self):
        self.assertEqual(parse_order_id("vx-10041"), "VX-10041")
        with self.assertRaises(ValidationError):
            parse_order_id("10041")
        with self.assertRaises(ValidationError):
            parse_order_id("VX-12")

    def test_reason_enum_and_amount_range(self):
        self.assertEqual(parse_reason("Damaged"), "damaged")
        with self.assertRaises(ValidationError):
            parse_reason("late")
        with self.assertRaises(ValidationError):
            parse_amount(0)
        with self.assertRaises(ValidationError):
            parse_amount(900)
        with self.assertRaises(ValidationError):
            parse_amount("nope")
        self.assertEqual(parse_amount("89.00"), 8900)


class PolicyTests(unittest.TestCase):
    def _order(self, **overrides):
        order = {
            "order_id": "VX-10043",
            "status": "delivered",
            "delivered_on": "2026-01-01",
            "refundable": 45.0,
            "refundable_cents": 4500,
        }
        order.update(overrides)
        return order

    def test_window_boundary(self):
        delivered = date(2026, 1, 1)
        inside = evaluate(self._order(), "changed_mind", 4500, today=delivered + timedelta(days=30))
        outside = evaluate(self._order(), "changed_mind", 4500, today=delivered + timedelta(days=31))
        self.assertTrue(inside["eligible"])
        self.assertFalse(outside["eligible"])
        self.assertIn("30 days", outside["explanation"])

    def test_defect_window_is_longer(self):
        delivered = date(2026, 1, 1)
        decision = evaluate(self._order(), "damaged", 4500, today=delivered + timedelta(days=40))
        self.assertTrue(decision["eligible"])

    def test_blocks_undelivered_cancelled_and_over_balance(self):
        self.assertIn("in_transit", evaluate(self._order(status="in_transit"), "damaged", 1000)["explanation"])
        self.assertIn("Cancelled", evaluate(self._order(status="cancelled"), "damaged", 1000)["explanation"])
        self.assertIn("fully refunded", evaluate(self._order(refundable=0, refundable_cents=0), "damaged", 1000)["explanation"])
        over = evaluate(self._order(), "damaged", 5000)
        self.assertFalse(over["eligible"])
        self.assertIn("exceeds", over["explanation"])


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CARE_DB"] = os.path.join(self.tmp.name, "care.db")
        os.environ["MEMORY_DIR"] = os.path.join(self.tmp.name, "memory")
        from mock_api import app

        self.cm = TestClient(app)
        self.http = self.cm.__enter__()
        self.api = from_test_client(self.http)
        self.maya = "maya.chen@example.com"
        self.jonah = "jonah.patel@example.com"
        self.amina = "amina.yusuf@example.com"

    def tearDown(self):
        self.cm.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_lookup_shipping_and_ownership(self):
        order = self.api.get_order(self.maya, "VX-10041")
        self.assertEqual(order["status"], "delivered")
        self.assertEqual(order["items"][0]["name"], "TrailBuds Headphones")
        shipping = self.api.get_shipping(self.maya, "VX-10042")
        self.assertEqual(shipping["tracking_number"], "1ZDEMO10042")
        hidden = self.api.get_order(self.jonah, "VX-10041")
        self.assertIn("error", hidden)
        self.assertNotIn("Maya", json.dumps(hidden))
        self.assertNotIn("TrailBuds", json.dumps(hidden))

    def test_eligibility_yes_and_no(self):
        yes = self.api.eligibility(self.maya, "VX-10041", "damaged", 89)
        no = self.api.eligibility(self.jonah, "VX-10043", "changed_mind", 45)
        refunded = self.api.eligibility(self.amina, "VX-10045", "damaged", 60)
        moving = self.api.eligibility(self.maya, "VX-10042", "changed_mind", 64)
        self.assertTrue(yes["eligible"])
        self.assertFalse(no["eligible"])
        self.assertFalse(refunded["eligible"])
        self.assertFalse(moving["eligible"])

    def test_refund_waits_for_a_person_then_is_idempotent(self):
        tools = CareTools(self.jonah, self.api)
        pending = tools.run(
            {
                "id": "refund",
                "name": "issue_refund",
                "arguments": {"order_id": "VX-10044", "reason": "damaged", "amount": 320},
            }
        )["result"]
        self.assertEqual(pending["status"], "awaiting_confirmation")
        self.assertTrue(pending["high_value"])
        self.assertNotIn("token", json.dumps(pending))
        self.assertEqual(self.api.get_order(self.jonah, "VX-10044")["refunded"], 0)

        blocked = self.http.post(
            f"/refunds/{pending['confirmation_id']}",
            headers={"X-Customer-Email": self.jonah, "X-Confirmation-Token": "not-the-token"},
        )
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(self.api.get_order(self.jonah, "VX-10044")["refunded"], 0)

        receipt = approve_and_execute(self.api, self.jonah, pending["confirmation_id"])
        again = approve_and_execute(self.api, self.jonah, pending["confirmation_id"])
        self.assertEqual(receipt["refund_id"], again["refund_id"])
        self.assertEqual(self.api.get_order(self.jonah, "VX-10044")["refunded"], 320)
        decisions = [event["decision"] for event in self.api.audit(self.jonah)]
        self.assertEqual(decisions, ["requested", "approved", "executed"])

    def test_cancel_in_transit_and_refuse_delivered(self):
        refused = self.api.request_cancel(self.maya, "VX-10041", "too late")
        self.assertEqual(refused["status"], "ineligible")
        pending = self.api.request_cancel(self.maya, "VX-10042", "ordered by mistake")
        self.assertEqual(pending["status"], "awaiting_confirmation")
        self.assertEqual(self.api.get_order(self.maya, "VX-10042")["status"], "in_transit")
        receipt = approve_and_execute(self.api, self.maya, pending["confirmation_id"])
        self.assertEqual(receipt["order_status"], "cancelled")

    def test_patterns(self):
        tools = CareTools(self.maya, self.api)
        parallel = parallel_lookup(tools, "VX-10042")
        self.assertEqual([step["name"] for step in parallel["steps"]], ["get_order", "get_shipping_status"])
        stopped = sequential_refund(tools, "VX-10042", "changed_mind", 64)
        self.assertEqual(stopped["stopped_after"], "get_order")
        self.assertEqual(len(stopped["steps"]), 1)
        checked = sequential_refund(tools, "VX-10041", "damaged", 89)
        self.assertTrue(checked["steps"][-1]["result"]["eligible"])

    def test_invalid_arguments_do_not_invent_an_order(self):
        result = CareTools(self.maya, self.api).run(
            {"id": "bad", "name": "get_order", "arguments": {"order_id": "VX-12"}}
        )["result"]
        self.assertIn("VX-#####", result["error"])

    def test_scripted_agent_cannot_refund_itself(self):
        seen = {"calls": 0}

        def complete(_messages, _tools):
            seen["calls"] += 1
            if seen["calls"] == 1:
                return {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "name": "issue_refund",
                            "arguments": {"order_id": "VX-10044", "reason": "damaged", "amount": 320},
                        }
                    ],
                }
            return {
                "content": "A supervisor still has to approve this refund. Nothing has been issued.",
                "tool_calls": [],
            }

        memory = CustomerMemory(directory=os.environ["MEMORY_DIR"], backend="local")
        result = run_turn(
            self.jonah,
            "Please issue a damaged refund of $320 for order VX-10044.",
            self.api,
            complete,
            memory,
        )
        self.assertEqual(result.stop_reason, "goal_met")
        self.assertEqual(result.trace[0]["result"]["status"], "awaiting_confirmation")
        self.assertIn("supervisor", result.reply.lower())
        self.assertEqual(self.api.get_order(self.jonah, "VX-10044")["refunded"], 0)
        self.assertIn("VX-10044", memory.recall(self.jonah))


class OrchestrationTests(unittest.TestCase):
    def test_parallel_calls_overlap(self):
        class Slow:
            def __init__(self):
                self.lock = threading.Lock()
                self.inflight = 0
                self.max_inflight = 0
                self.opened = threading.Event()

            def run(self, call):
                with self.lock:
                    self.inflight += 1
                    self.max_inflight = max(self.max_inflight, self.inflight)
                    if self.inflight >= 2:
                        self.opened.set()
                self.opened.wait(1)
                with self.lock:
                    self.inflight -= 1
                return {"id": call["id"], "name": call["name"], "arguments": {}, "result": {"ok": True}}

        tools = Slow()
        execute_calls(
            tools,
            [
                {"id": "a", "name": "get_order"},
                {"id": "b", "name": "get_shipping_status"},
            ],
        )
        self.assertEqual(tools.max_inflight, 2)

    def test_routes(self):
        self.assertEqual(conditional_route("Look up order VX-10041."), "order_lookup")
        self.assertEqual(conditional_route("Where is order VX-10042?"), "parallel_lookup")
        self.assertEqual(conditional_route("Please refund VX-10044."), "sequential_refund")
        self.assertEqual(conditional_route("just fix it"), "escalate_to_human")


if __name__ == "__main__":
    unittest.main()
