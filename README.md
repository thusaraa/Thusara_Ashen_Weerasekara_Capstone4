# Capstone 4 — Customer Care Agent

A support agent answers order, shipping, and refund questions from a mock commerce API. Reads run on their own. A refund or a cancellation only creates a confirmation. A supervisor has to approve it before the API changes the order.

This is capstone 4 of 5 in the course brief (the Module 3 customer-care build). The account data is seeded in SQLite and served by FastAPI. Chat uses the same OpenRouter key as the earlier capstones.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```


Long-term memory uses Upstash Redis when both of these are set:

```text
UPSTASH_REDIS_REST_URL
UPSTASH_REDIS_REST_TOKEN
```

Without them, each customer is a file in `memory_store/`.

## Run

```powershell
.\.venv\Scripts\python.exe test_care.py
.\.venv\Scripts\python.exe run_demo.py
.\.venv\Scripts\python.exe customer_care_agent.py "Look up order VX-10041." --customer maya.chen@example.com
.\.venv\Scripts\python.exe human_gate.py conf_xxxxxxxx --customer jonah.patel@example.com
```

`run_demo.py` reseeds `care.db`, runs the five scenarios, and writes `demo_run.md`. The agent process starts the mock API on localhost.

## Policy

| Reason | Window after delivery | Notes |
| --- | --- | --- |
| changed_mind, late_delivery | 30 days | Order must be delivered |
| damaged, wrong_item, not_as_described | 45 days | Order must be delivered |
| any refund | amount from $0.01 to $500, and not above the refundable balance | $200 and over is flagged high value |
| cancel | placed or in_transit only | Delivered orders are refunds, not cancellations |

Order ids match `VX-#####`. A customer can only see their own orders.

## Seeded accounts

| Order | Customer | What it is for |
| --- | --- | --- |
| VX-10041 | maya.chen@example.com | Headphones, delivered 12 days ago, $89. Damaged refund is eligible. |
| VX-10042 | maya.chen@example.com | Cable, in transit. Tracking and ETA. Can be cancelled with approval. |
| VX-10043 | jonah.patel@example.com | Shirt, delivered 50 days ago, $45. A changed-mind refund is outside the window. |
| VX-10044 | jonah.patel@example.com | Camera, delivered 6 days ago, $320. High-value damaged refund, needs approval. |
| VX-10045 | amina.yusuf@example.com | Beanie already refunded in full. |
| VX-10046 | amina.yusuf@example.com | Bag already cancelled. |

## What each file does

- `mock_api.py` — orders, shipping, eligibility, refund and cancel requests, and the audit log.
- `store.py` — the SQLite database behind that API.
- `gate.py` — eligibility rules, and the split between requesting a write and applying it.
- `human_gate.py` — the supervisor approval. It is not a model tool.
- `tools.py` — the six tool schemas and the argument checks (order id, reason, amount).
- `orchestration.py` — sequential refund checks, parallel order-and-shipping reads, and the confidence route.
- `customer_care_agent.py` — the tool-calling loop.
- `memory.py` — earlier turns, order ids, and complaint reasons for one customer.
- `run_demo.py` — the five scenarios, from a lookup through a refund that waits for approval.
