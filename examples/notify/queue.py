"""Retry queue for failed notification deliveries."""

from __future__ import annotations

import time
from collections.abc import Callable

MAX_ATTEMPTS = 5


class PermanentError(Exception):
    """Delivery can never succeed (e.g. 4xx from the receiver)."""


class TransientError(Exception):
    """Delivery may succeed later (timeouts, 5xx)."""


def deliver_with_retry(send: Callable[[], int], base_delay: float = 0.5) -> bool:
    attempt = 0
    while attempt < MAX_ATTEMPTS:
        try:
            status = send()
        except PermanentError:
            return False
        except TransientError:
            time.sleep(base_delay * 2**attempt)
            continue
        if status < 300:
            return True
        attempt += 1
    return False


def batches(items: list[str], size: int = 100) -> list[list[str]]:
    if size <= 0:
        raise ValueError("size must be positive")
    return [items[i : i + size] for i in range(0, len(items), size)]
