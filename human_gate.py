"""Supervisor step. The model cannot call this.

Approving a confirmation writes a token the commerce API will accept once.
The token is not included in the receipt returned to the agent.
"""

from __future__ import annotations

import argparse
import json

from client import CommerceClient
from gate import approve
from runtime import ensure_server
from store import StoreError
import store
from validators import parse_email


def approve_and_execute(client: CommerceClient, email: str, confirmation_id: str) -> dict:
    approval = approve(confirmation_id.strip(), actor="supervisor")
    if approval["email"] != email:
        raise StoreError("This confirmation belongs to a different customer.", 403)
    token = approval.get("token")
    if not token:
        raise StoreError("Approval did not produce a token.", 409)
    if approval["action"] == "issue_refund":
        receipt = client.execute_refund(email, approval["confirmation_id"], token)
    elif approval["action"] == "cancel_order":
        receipt = client.execute_cancel(email, approval["confirmation_id"], token)
    else:
        raise StoreError("Unsupported confirmation.", 400)
    return {key: value for key, value in receipt.items() if key != "token"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Approve one pending refund or cancellation")
    parser.add_argument("confirmation_id")
    parser.add_argument("--customer", required=True)
    args = parser.parse_args()
    store.init_db()
    client = CommerceClient(base_url=ensure_server())
    receipt = approve_and_execute(client, parse_email(args.customer), args.confirmation_id)
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
