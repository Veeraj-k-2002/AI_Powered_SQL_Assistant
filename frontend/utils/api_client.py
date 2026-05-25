"""
frontend/utils/api_client.py
──────────────────────────────
Thin wrapper around the FastAPI backend.
All Streamlit pages import from here — one place to change the base URL.
"""

import requests
import os
from typing import Any

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
BASE = f"{BACKEND_URL}/api/v1"


def _handle(response: requests.Response) -> Any:
    """Raise a RuntimeError with the API error detail if the call failed."""
    if not response.ok:
        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text
        raise RuntimeError(f"API error {response.status_code}: {detail}")
    return response.json()


# ── Databases ─────────────────────────────────────────────────────────────────

def list_databases() -> list[dict]:
    return _handle(requests.get(f"{BASE}/databases/"))


def create_database(data: dict) -> dict:
    return _handle(requests.post(f"{BASE}/databases/", json=data))


def delete_database(database_id: str) -> dict:
    return _handle(requests.delete(f"{BASE}/databases/{database_id}"))


def test_connection(database_id: str) -> dict:
    return _handle(requests.get(f"{BASE}/databases/{database_id}/test"))


# ── Chat ──────────────────────────────────────────────────────────────────────

def send_message(database_id: str, user_query: str) -> dict:
    return _handle(
        requests.post(
            f"{BASE}/chat/ask",
            json={"database_id": database_id, "user_query": user_query},
            timeout=120,   # LLM calls can be slow
        )
    )


# ── History ───────────────────────────────────────────────────────────────────

def get_history(database_id: str, limit: int = 100) -> dict:
    return _handle(
        requests.get(
            f"{BASE}/history/database/{database_id}",
            params={"limit": limit},
        )
    )


def clear_history(database_id: str) -> dict:
    return _handle(requests.delete(f"{BASE}/history/database/{database_id}"))
