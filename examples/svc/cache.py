"""In-memory cache for user profiles."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Loader = Callable[[str, int], dict[str, Any]]


class ProfileCache:
    def __init__(self, loader: Loader, max_size: int = 1024) -> None:
        self._loader = loader
        self._max_size = max_size
        self._data: dict[int, dict[str, Any]] = {}

    def get(self, tenant_id: str, user_id: int) -> dict[str, Any]:
        if user_id not in self._data:
            if len(self._data) >= self._max_size:
                self._data.pop(next(iter(self._data)))
            self._data[user_id] = self._loader(tenant_id, user_id)
        return self._data[user_id]

    def invalidate(self, user_id: int) -> None:
        self._data.pop(user_id, None)
