"""Per-client token-bucket rate limiter."""

from __future__ import annotations

import threading
import time


class TokenBucket:
    def __init__(self, capacity: int, refill_per_sec: float) -> None:
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self.tokens = float(capacity)
        self.updated = time.time()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.time()
        elapsed = max(0.0, now - self.updated)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)
        self.updated = now

    def allow(self, cost: int = 1) -> bool:
        self._refill()
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False


class Limiter:
    def __init__(self, capacity: int = 20, refill_per_sec: float = 5.0) -> None:
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self._buckets: dict[str, TokenBucket] = {}

    def allow(self, client_id: str, cost: int = 1) -> bool:
        bucket = self._buckets.get(client_id)
        if bucket is None:
            bucket = TokenBucket(self.capacity, self.refill_per_sec)
            self._buckets[client_id] = bucket
        return bucket.allow(cost)
