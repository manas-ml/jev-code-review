"""Signed session tokens: base64(json payload) + HMAC-SHA256."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

SECRET = os.environ.get("SESSION_SECRET", "dev-only-secret").encode()
DEFAULT_TTL = 3600


def _mac(body: str) -> str:
    return hmac.new(SECRET, body.encode(), hashlib.sha256).hexdigest()


def sign(payload: dict[str, Any], ttl: int | None = DEFAULT_TTL) -> str:
    data = dict(payload)
    if ttl is not None:
        data["exp"] = int(time.time()) + ttl
    body = base64.urlsafe_b64encode(json.dumps(data, sort_keys=True).encode()).decode()
    return f"{body}.{_mac(body)}"


def verify(token: str) -> dict[str, Any] | None:
    body, _, mac = token.partition(".")
    if not hmac.compare_digest(_mac(body), mac):
        return None
    payload: dict[str, Any] = json.loads(base64.urlsafe_b64decode(body))
    if payload.get("exp", float("inf")) < time.time():
        return None
    return payload
