# Capstone 4 demo — customer care

Read tools answer on their own. `issue_refund` only creates a confirmation. A supervisor approves it, and the API then writes the refund.

## Patterns

Parallel lookup of VX-10042 returned get_order, get_shipping_status. Tracking 1ZDEMO10042, status in_transit.

Sequential refund check for VX-10041 stopped after check_refund_eligibility. Eligible: True.

Sequential refund check for in-transit VX-10042 stopped after get_order. Eligibility was not called.

Sequential check for VX-10043 changed_mind: changed_mind refunds must be requested within 30 days of delivery. This order was delivered 50 days ago.

Conditional routes:
- `Look up order VX-10041.` -> order_lookup
- `Where is order VX-10042?` -> parallel_lookup
- `Please refund order VX-10044.` -> sequential_refund
- `just fix it` -> escalate_to_human

## Five scenarios

The agent used the tool loop below. Scenario 5 is approved only in the supervisor step.

### 1. Order lookup

Customer (maya.chen@example.com): Look up order VX-10041 and tell me what I bought and whether it was delivered.

Stop: goal_met. Tokens in 1631, out 105.

Tools:

- `get_order` {"order_id": "VX-10041"}
  {"order_id": "VX-10041", "email": "maya.chen@example.com", "customer_name": "Maya Chen", "status": "delivered", "total": 89.0, "refunded": 0.0, "refundable": 89.0, "total_cents": 8900, "refunded_cents": 0, "refundable_cents": 8900, "placed_on": "2026-10-24", "delivered_on": "2026-10-26", "items": [{"sku": "HP-100", "name": "TrailBuds Headphones", "qty": 1, "price": 89.0}]}
- `get_shipping_status` {"order_id": "VX-10041"}
  {"order_id": "VX-10041", "carrier": "UPS", "tracking_number": "1ZDEMO10041", "status": "delivered", "eta": "2026-10-26", "last_scan": "Left at the front door"}

You purchased 1 TrailBuds Headphones for a total of $89.00 in order VX-10041. The order has been delivered, with the last scan indicating it was left at the front door on October 26, 2026.

### 2. Shipping ETA

Customer (maya.chen@example.com): Where is order VX-10042? I need the tracking number and the ETA.

Stop: goal_met. Tokens in 1789, out 103.

Tools:

- `get_order` {"order_id": "VX-10042"}
  {"order_id": "VX-10042", "email": "maya.chen@example.com", "customer_name": "Maya Chen", "status": "in_transit", "total": 64.0, "refunded": 0.0, "refundable": 64.0, "total_cents": 6400, "refunded_cents": 0, "refundable_cents": 6400, "placed_on": "2026-11-05", "delivered_on": null, "items": [{"sku": "CB-200", "name": "USB-C Cable 2-pack", "qty": 1, "price": 64.0}]}
- `get_shipping_status` {"order_id": "VX-10042"}
  {"order_id": "VX-10042", "carrier": "UPS", "tracking_number": "1ZDEMO10042", "status": "in_transit", "eta": "2026-11-11", "last_scan": "Memphis, TN"}

Your order VX-10042, which includes 1 USB-C Cable 2-pack, is currently in transit. The tracking number is 1ZDEMO10042, and the estimated arrival date is November 11, 2026.

### 3. Eligible refund check

Customer (maya.chen@example.com): Is a damaged refund of $89.00 eligible for order VX-10041? Do not issue it yet.

Stop: goal_met. Tokens in 1812, out 79.

Tools:

- `check_refund_eligibility` {"order_id": "VX-10041", "reason": "damaged", "amount": 89}
  {"eligible": true, "order_id": "VX-10041", "order_status": "delivered", "reason": "damaged", "amount": 89.0, "refundable_balance": 89.0, "window_days": 45, "days_since_delivery": 12, "high_value": false, "explanation": "Eligible for a damaged refund of $89.00. A supervisor still has to approve the write."}

Yes, a damaged refund of $89.00 is eligible for order VX-10041. However, a supervisor must approve the refund before it can be processed. Let me know if you would like to proceed with issuing the refund.

### 4. Ineligible refund

Customer (jonah.patel@example.com): I changed my mind about order VX-10043. Can I get $45.00 back?

Stop: goal_met. Tokens in 1688, out 136.

Tools:

- `get_order` {"order_id": "VX-10043"}
  {"order_id": "VX-10043", "email": "jonah.patel@example.com", "customer_name": "Jonah Patel", "status": "delivered", "total": 45.0, "refunded": 0.0, "refundable": 45.0, "total_cents": 4500, "refunded_cents": 0, "refundable_cents": 4500, "placed_on": "2026-09-13", "delivered_on": "2026-09-18", "items": [{"sku": "SH-310", "name": "Linen Shirt", "qty": 1, "price": 45.0}]}
- `check_refund_eligibility` {"order_id": "VX-10043", "reason": "changed_mind", "amount": 45}
  {"eligible": false, "order_id": "VX-10043", "order_status": "delivered", "reason": "changed_mind", "amount": 45.0, "refundable_balance": 45.0, "window_days": 30, "days_since_delivery": 50, "high_value": false, "explanation": "changed_mind refunds must be requested within 30 days of delivery. This order was delivered 50 days ago."}

Unfortunately, your request for a refund of $45.00 for order VX-10043 due to a change of mind is ineligible. Refunds for changed minds must be requested within 30 days of delivery, and this order was delivered 50 days ago. If you have any other questions or need further assistance, feel free to ask!

### 5. Refund with approval

Customer (jonah.patel@example.com): Please issue a damaged refund of $320.00 for order VX-10044.

Stop: goal_met. Tokens in 4079, out 125.

Tools:

- `get_order` {"order_id": "VX-10044"}
  {"order_id": "VX-10044", "email": "jonah.patel@example.com", "customer_name": "Jonah Patel", "status": "delivered", "total": 320.0, "refunded": 0.0, "refundable": 320.0, "total_cents": 32000, "refunded_cents": 0, "refundable_cents": 32000, "placed_on": "2026-10-29", "delivered_on": "2026-11-01", "items": [{"sku": "CM-440", "name": "Mirrorless Camera", "qty": 1, "price": 320.0}]}
- `check_refund_eligibility` {"order_id": "VX-10044", "reason": "damaged", "amount": 320}
  {"eligible": true, "order_id": "VX-10044", "order_status": "delivered", "reason": "damaged", "amount": 320.0, "refundable_balance": 320.0, "window_days": 45, "days_since_delivery": 6, "high_value": true, "explanation": "Eligible for a damaged refund of $320.00. A supervisor still has to approve the write."}
- `issue_refund` {"order_id": "VX-10044", "reason": "damaged", "amount": 320}
  {"status": "awaiting_confirmation", "confirmation_id": "conf_d274214e", "action": "issue_refund", "order_id": "VX-10044", "amount": 320.0, "reason": "damaged", "high_value": true, "message": "Waiting for a supervisor. This refund is over $200, so it is flagged high value. Nothing has been changed on the order."}

I've requested a damaged refund of $320.00 for order VX-10044. A supervisor must approve this request, and the confirmation ID is **conf_d274214e**. Please let me know if you need further assistance!

Supervisor receipt:

```json
{
  "status": "executed",
  "action": "issue_refund",
  "refund_id": 2,
  "confirmation_id": "conf_d274214e",
  "order_id": "VX-10044",
  "amount": 320.0,
  "reason": "damaged",
  "high_value": true
}
```

Your refund of $320.00 for order VX-10044 has been successfully processed due to the damaged item. If you have any further questions or need assistance, feel free to reach out!


## Governed refund

VX-10044 was refunded through the agent confirmation above. Refunded amount: $320.00. The order total was $320.00.

## Audit

### maya.chen@example.com

No write or escalation events.

### jonah.patel@example.com

- requested issue_refund on VX-10044 by agent: {"confirmation_id": "conf_d274214e", "reason": "damaged", "amount": 320.0, "high_value": true}
- approved issue_refund on VX-10044 by supervisor: {"confirmation_id": "conf_d274214e", "high_value": true}
- executed issue_refund on VX-10044 by system: {"confirmation_id": "conf_d274214e", "amount": 320.0, "reason": "damaged"}
