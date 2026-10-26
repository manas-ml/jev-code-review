"""Async retry with exponential backoff."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class TransientError(Exception):
    pass


async def retry(fn: Callable[[], Awaitable[T]], attempts: int = 3, base: float = 0.2) -> T:
    for i in range(attempts):
        try:
            return await fn()
        except TransientError:
            if i == attempts - 1:
                raise
            await asyncio.sleep(base * 2**i)
    raise RuntimeError("unreachable")
