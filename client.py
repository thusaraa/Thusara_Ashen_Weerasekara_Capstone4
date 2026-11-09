"""HTTP client for the mock commerce API."""

from __future__ import annotations

import json

import httpx


class CommerceClient:
    def __init__(self, base_url: str | None = None, request=None):
        self.base_url = (base_url or "").rstrip("/")
        self._request = request
        self._http = None
        if request is None:
            if not self.base_url:
                raise ValueError("base_url is required")
            self._http = httpx.Client(base_url=self.base_url, timeout=30)

    def close(self) -> None:
        if self._http is not None:
            self._http.close()

    def get_order(self, email: str, order_id: str) -> dict:
        return self._unwrap(*self._send("GET", f"/orders/{order_id}", email))

    def get_shipping(self, email: str, order_id: str) -> dict:
        return self._unwrap(*self._send("GET", f"/orders/{order_id}/shipping", email))

    def eligibility(self, email: str, order_id: str, reason: str, amount: float) -> dict:
        return self._unwrap(
            *self._send(
                "POST",
                "/refunds/eligibility",
                email,
                {"order_id": order_id, "reason": reason, "amount": amount},
            )
        )

    def request_refund(self, email: str, order_id: str, reason: str, amount: float) -> dict:
        return self._unwrap(
            *self._send(
                "POST",
                "/refunds/requests",
                email,
                {"order_id": order_id, "reason": reason, "amount": amount},
            )
        )

    def execute_refund(self, email: str, confirmation_id: str, token: str) -> dict:
        return self._unwrap(
            *self._send("POST", f"/refunds/{confirmation_id}", email, token=token)
        )

    def request_cancel(self, email: str, order_id: str, note: str) -> dict:
        return self._unwrap(
            *self._send(
                "POST",
                "/cancellations/requests",
                email,
                {"order_id": order_id, "note": note},
            )
        )

    def execute_cancel(self, email: str, confirmation_id: str, token: str) -> dict:
        return self._unwrap(
            *self._send("POST", f"/cancellations/{confirmation_id}", email, token=token)
        )

    def audit(self, email: str) -> list[dict]:
        body = self._unwrap(*self._send("GET", "/audit", email))
        if isinstance(body, dict) and "events" in body:
            return body["events"]
        return body

    def _send(self, method: str, path: str, email: str, payload: dict | None = None, token: str | None = None):
        headers = {"X-Customer-Email": email}
        if token:
            headers["X-Confirmation-Token"] = token
        if self._request is not None:
            return self._request(method, path, payload, headers)
        kwargs = {"headers": headers}
        if payload is not None:
            kwargs["json"] = payload
        response = self._http.request(method, path, **kwargs)
        try:
            body = response.json()
        except json.JSONDecodeError:
            body = {"error": response.text}
        return response.status_code, body

    @staticmethod
    def _unwrap(status: int, body: dict) -> dict:
        if status < 400:
            return body
        if isinstance(body, dict) and "error" in body:
            return body
        return {"error": str(body)}


def from_test_client(test_client) -> CommerceClient:
    def request(method, path, payload, headers):
        kwargs = {"headers": headers}
        if payload is not None:
            kwargs["json"] = payload
        response = test_client.request(method, path, **kwargs)
        try:
            body = response.json()
        except json.JSONDecodeError:
            body = {"error": response.text}
        return response.status_code, body

    return CommerceClient(request=request)
