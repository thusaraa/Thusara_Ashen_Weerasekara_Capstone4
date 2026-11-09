"""Mock commerce API. The agent reaches orders through these routes only.

Refund and cancel routes refuse to change an order unless the caller sends
the supervisor token created by gate.approve.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import store
from gate import apply_cancel, apply_refund, evaluate, request_cancel, request_refund
from store import StoreError, owned_order
from validators import ValidationError, parse_amount, parse_email, parse_order_id, parse_reason


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store.init_db()
    yield


app = FastAPI(title="Capstone 4 commerce API", lifespan=lifespan)


@app.exception_handler(StoreError)
def handle_store_error(_request, exc: StoreError):
    return JSONResponse(status_code=exc.status, content={"error": exc.message})


@app.exception_handler(ValidationError)
def handle_validation_error(_request, exc: ValidationError):
    return JSONResponse(status_code=422, content={"error": str(exc)})


def customer_email(x_customer_email: str = Header(alias="X-Customer-Email")) -> str:
    return parse_email(x_customer_email)


def confirmation_token(x_confirmation_token: str = Header(alias="X-Confirmation-Token")) -> str:
    token = (x_confirmation_token or "").strip()
    if not token:
        raise ValidationError("X-Confirmation-Token is required")
    return token


class RefundIn(BaseModel):
    order_id: str
    reason: str
    amount: float


class CancelIn(BaseModel):
    order_id: str
    note: str = ""


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/orders/{order_id}")
def read_order(order_id: str, email: str = Depends(customer_email)):
    return owned_order(email, parse_order_id(order_id))


@app.get("/orders/{order_id}/shipping")
def read_shipping(order_id: str, email: str = Depends(customer_email)):
    parsed = parse_order_id(order_id)
    owned_order(email, parsed)
    shipping = store.get_shipping(parsed)
    if shipping is None:
        raise StoreError("No shipment is recorded for this order.", 404)
    return shipping


@app.post("/refunds/eligibility")
def refund_eligibility(body: RefundIn, email: str = Depends(customer_email)):
    order_id = parse_order_id(body.order_id)
    reason = parse_reason(body.reason)
    amount_cents = parse_amount(body.amount)
    return evaluate(owned_order(email, order_id), reason, amount_cents)


@app.post("/refunds/requests")
def refund_request(body: RefundIn, email: str = Depends(customer_email)):
    return request_refund(
        email,
        parse_order_id(body.order_id),
        parse_reason(body.reason),
        parse_amount(body.amount),
    )


@app.post("/refunds/{confirmation_id}")
def refund_execute(
    confirmation_id: str,
    email: str = Depends(customer_email),
    token: str = Depends(confirmation_token),
):
    return apply_refund(email, confirmation_id, token)


@app.post("/cancellations/requests")
def cancel_request(body: CancelIn, email: str = Depends(customer_email)):
    return request_cancel(email, parse_order_id(body.order_id), body.note.strip())


@app.post("/cancellations/{confirmation_id}")
def cancel_execute(
    confirmation_id: str,
    email: str = Depends(customer_email),
    token: str = Depends(confirmation_token),
):
    return apply_cancel(email, confirmation_id, token)


@app.get("/audit")
def read_audit(email: str = Depends(customer_email)):
    return {"email": email, "events": store.list_audit(email)}
