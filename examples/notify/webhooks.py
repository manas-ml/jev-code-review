"""Deliver notifications to customer-configured webhook URLs."""

from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import urlparse

import httpx

BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254", "metadata.google.internal"}


def validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("unsupported scheme")
    if (parsed.hostname or "").lower() in BLOCKED_HOSTS:
        raise ValueError("blocked host")
    return url


def sign(secret: bytes, body: bytes) -> str:
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def verify_ack(secret: bytes, body: bytes, signature: str) -> bool:
    return hmac.compare_digest(sign(secret, body), signature)


def deliver(client: httpx.Client, url: str, secret: bytes, event: dict) -> int:
    body = json.dumps(event, sort_keys=True).encode()
    resp = client.post(
        validate_url(url),
        content=body,
        headers={"X-Signature": sign(secret, body), "Content-Type": "application/json"},
        timeout=5.0,
    )
    return resp.status_code
