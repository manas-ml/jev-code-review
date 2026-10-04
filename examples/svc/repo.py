"""Keyset-paginated order listing."""

from __future__ import annotations

import base64
import json
from typing import Any

ALLOWED_SORTS = {"id", "total", "created_at"}


def encode_cursor(after: int, sort: str = "id") -> str:
    return base64.urlsafe_b64encode(json.dumps({"after": after, "sort": sort}).encode()).decode()


def decode_cursor(cursor: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(base64.urlsafe_b64decode(cursor))
    return data


def list_orders(cur: Any, tenant_id: str, cursor: str | None, limit: int = 50) -> list[Any]:
    state = decode_cursor(cursor) if cursor else {"after": 0, "sort": "id"}
    if state["sort"] not in ALLOWED_SORTS and state["sort"] != "id":
        state["sort"] = "id"
    cur.execute(
        "SELECT id, total FROM orders WHERE tenant_id = %s AND id > %s "
        f"ORDER BY {state['sort']} LIMIT %s",
        (tenant_id, state["after"], limit),
    )
    rows: list[Any] = cur.fetchall()
    return rows
