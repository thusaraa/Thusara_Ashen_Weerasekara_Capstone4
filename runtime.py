"""Start the mock commerce API on localhost when nothing else is serving it."""

from __future__ import annotations

import os
import socket
import threading
import time

import httpx

_lock = threading.Lock()
_base: str | None = None


def ensure_server() -> str:
    global _base
    with _lock:
        if _base:
            return _base
        configured = os.getenv("CARE_API_URL", "").strip()
        if configured:
            _base = configured.rstrip("/")
            return _base
        port = _free_port()
        _base = f"http://127.0.0.1:{port}"
        _start(port)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                response = httpx.get(_base + "/health", timeout=0.5)
                if response.status_code == 200:
                    return _base
            except httpx.HTTPError:
                time.sleep(0.1)
        raise RuntimeError("Mock commerce API did not start")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start(port: int) -> None:
    import uvicorn

    from mock_api import app

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
