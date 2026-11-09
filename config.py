"""Shared settings for the customer-care capstone.

Chat calls use OpenAI when OPENAI_API_KEY is set, and otherwise OpenRouter.
The same key from Capstone 1–3 works here.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# changed_mind and late_delivery. Defect reasons use the longer window.
STANDARD_WINDOW_DAYS = 30
DEFECT_WINDOW_DAYS = 45
DEFECT_REASONS = frozenset({"damaged", "wrong_item", "not_as_described"})

# Schema limits. The order balance is a second, tighter limit.
HIGH_VALUE_CENTS = 20_000
MAX_REFUND_CENTS = 50_000

MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "6"))


def db_path() -> Path:
    return Path(os.getenv("CARE_DB", str(ROOT / "care.db")))


def memory_dir() -> Path:
    return Path(os.getenv("MEMORY_DIR", str(ROOT / "memory_store")))


def chat_client_kwargs() -> dict:
    """Arguments for the OpenAI SDK. base_url is omitted for the official API."""
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        return {
            "model": os.getenv("CHAT_MODEL", "gpt-4o-mini"),
            "api_key": openai_key,
            "temperature": 0,
        }

    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if openrouter_key:
        return {
            "model": os.getenv("CHAT_MODEL", "openai/gpt-4o-mini"),
            "api_key": openrouter_key,
            "base_url": OPENROUTER_BASE_URL,
            "temperature": 0,
        }

    raise RuntimeError("Set OPENAI_API_KEY or OPENROUTER_API_KEY in .env")
