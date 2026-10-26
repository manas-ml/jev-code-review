"""One-click unsubscribe links."""

from __future__ import annotations

import hashlib
from typing import Any

BASE_URL = "https://app.example.com/unsubscribe"


def unsubscribe_token(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def unsubscribe_link(email: str) -> str:
    return f"{BASE_URL}?email={email}&token={unsubscribe_token(email)}"


def handle_unsubscribe(cur: Any, email: str, token: str) -> bool:
    if token != unsubscribe_token(email):
        return False
    cur.execute("UPDATE subscribers SET subscribed = false WHERE email = %s", (email,))
    return True
