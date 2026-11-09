"""Per-customer conversation memory.

Upstash Redis is used when UPSTASH_REDIS_REST_URL and
UPSTASH_REDIS_REST_TOKEN are both set. Otherwise each customer is a JSON
file under memory_store/.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

from config import memory_dir
from validators import ORDER_ID_RE


class CustomerMemory:
    def __init__(self, directory=None, backend: str | None = None):
        self.directory = memory_dir() if directory is None else Path(directory)
        self.url = os.getenv("UPSTASH_REDIS_REST_URL", "").rstrip("/")
        self.token = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")
        if backend:
            self.backend = backend
        else:
            self.backend = "upstash" if self.url and self.token else "local"

    def recall(self, email: str) -> str:
        record = self._load(email)
        turns = record.get("turns") or []
        if not turns:
            return "No earlier conversation."
        lines = []
        for turn in turns[-4:]:
            lines.append(f"Customer: {turn.get('user', '')}")
            lines.append(f"Agent: {turn.get('assistant', '')}")
        orders = ", ".join(record.get("order_ids") or []) or "none"
        complaints = record.get("complaints") or []
        complaint_line = "; ".join(complaints[-3:]) if complaints else "none"
        return (
            "Recent conversation:\n"
            + "\n".join(lines)
            + f"\nOrders mentioned: {orders}\nComplaints: {complaint_line}"
        )

    def save_turn(self, email: str, user: str, assistant: str, trace: list[dict]) -> None:
        record = self._load(email)
        record.setdefault("turns", []).append(
            {
                "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "user": user[:600],
                "assistant": assistant[:600],
            }
        )
        record["turns"] = record["turns"][-6:]
        found = set(record.get("order_ids") or [])
        found.update(ORDER_ID_RE.findall(user + " " + assistant))
        for step in trace:
            blob = json.dumps(step.get("result", ""))
            found.update(ORDER_ID_RE.findall(blob))
            reason = (step.get("arguments") or {}).get("reason")
            order_id = (step.get("arguments") or {}).get("order_id")
            if reason and order_id:
                record.setdefault("complaints", []).append(f"{order_id} {reason}")
        record["order_ids"] = sorted(found)
        record["complaints"] = record.get("complaints", [])[-6:]
        self._store(email, record)

    def clear_customer(self, email: str) -> None:
        if self.backend == "upstash":
            try:
                self._command("DEL", self._key(email))
                return
            except (requests.RequestException, RuntimeError):
                self.backend = "local"
        path = self._path(email)
        if path.exists():
            path.unlink()

    def _load(self, email: str) -> dict:
        if self.backend == "upstash":
            try:
                raw = self._command("GET", self._key(email))
                return json.loads(raw) if raw else {}
            except (requests.RequestException, json.JSONDecodeError, RuntimeError):
                self.backend = "local"
        return self._read_local(email)

    def _store(self, email: str, record: dict) -> None:
        if self.backend == "upstash":
            try:
                self._command("SET", self._key(email), json.dumps(record))
                return
            except (requests.RequestException, RuntimeError):
                self.backend = "local"
        self._write_local(email, record)

    def _command(self, *parts: str):
        response = requests.post(
            self.url,
            headers={"Authorization": f"Bearer {self.token}"},
            json=list(parts),
            timeout=15,
        )
        response.raise_for_status()
        body = response.json()
        if isinstance(body, dict) and body.get("error"):
            raise RuntimeError(body["error"])
        return body.get("result") if isinstance(body, dict) else body

    def _key(self, email: str) -> str:
        return f"capstone4:customer:{email}"

    def _path(self, email: str):
        safe = re.sub(r"[^a-z0-9]+", "_", email.lower()).strip("_")
        return self.directory / f"{safe}.json"

    def _read_local(self, email: str) -> dict:
        path = self._path(email)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _write_local(self, email: str, record: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(email).write_text(json.dumps(record, indent=2), encoding="utf-8")
