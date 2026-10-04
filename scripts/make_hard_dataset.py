"""Generate the HARD synthetic dataset into eval/dataset_hard (deterministic, no network).

Each case is a multi-file PR with realistic modules, subtle bugs (labels.issues) and decoys:
code that looks wrong but is correct (labels.must_not_flag). A reviewer that comments on a decoy
region scores a decoy hit (false positive). Some cases are clean-but-scary (decoys only).

Line numbers are located by a unique marker substring in the NEW file.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from pathlib import Path

OUT = Path("eval/dataset_hard")


@dataclass
class Case:
    id: str
    title: str
    description: str
    files: list[tuple[str, str, str]]  # (path, base, new); base "" = new file
    bugs: list[tuple[str, str, str]] = field(default_factory=list)  # (path, marker, desc)
    decoys: list[tuple[str, str, str]] = field(default_factory=list)


CASES: list[Case] = []


def add(c: Case) -> None:
    CASES.append(c)


# ---------------------------------------------------------------- 1 race on inventory
add(
    Case(
        "hard-inventory-race",
        "Add stock reservations",
        "Reserve and release stock per SKU.",
        [
            (
                "shop/inventory.py",
                "",
                '''"""In-memory stock levels with reservations."""

from __future__ import annotations

import threading
from collections import defaultdict


class Inventory:
    def __init__(self) -> None:
        self._stock: dict[str, int] = defaultdict(int)
        self._reserved: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def restock(self, sku: str, qty: int) -> None:
        if qty <= 0:
            raise ValueError("qty must be positive")
        with self._lock:
            self._stock[sku] += qty

    def available(self, sku: str) -> int:
        return max(0, self._stock[sku] - self._reserved[sku])

    def reserve(self, sku: str, qty: int) -> bool:
        if self.available(sku) >= qty:
            self._reserved[sku] += qty
            return True
        return False

    def release(self, sku: str, qty: int) -> None:
        with self._lock:
            self._reserved[sku] = max(0, self._reserved[sku] - qty)
''',
            ),
            (
                "shop/test_inventory.py",
                "",
                """from shop.inventory import Inventory


def test_reserve_and_release():
    inv = Inventory()
    inv.restock("a", 2)
    assert inv.reserve("a", 2)
    assert not inv.reserve("a", 1)
    inv.release("a", 2)
    assert inv.available("a") == 2
""",
            ),
        ],
        bugs=[
            (
                "shop/inventory.py",
                "if self.available(sku) >= qty:",
                "reserve() checks and increments without the lock: concurrent reservations oversell",
            )
        ],
        decoys=[
            (
                "shop/inventory.py",
                "return max(0, self._stock[sku] - self._reserved[sku])",
                "clamping available at 0 is intentional and correct",
            ),
            (
                "shop/inventory.py",
                "self._reserved[sku] = max(0, self._reserved[sku] - qty)",
                "release under lock with clamp is correct",
            ),
        ],
    )
)

# ---------------------------------------------------------------- 2 token expiry
add(
    Case(
        "hard-token-exp",
        "Harden API token verification",
        "Verify signed API tokens.",
        [
            (
                "api/tokens.py",
                """import hashlib
import hmac
import json


def verify(secret: bytes, token: str) -> dict | None:
    body, _, sig = token.rpartition(".")
    if hmac.new(secret, body.encode(), hashlib.sha256).hexdigest() != sig:
        return None
    return json.loads(body)
""",
                """import hashlib
import hmac
import json
import time


def verify(secret: bytes, token: str) -> dict | None:
    body, _, sig = token.rpartition(".")
    expected = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    claims = json.loads(body)
    if claims.get("exp") and claims["exp"] < time.time():
        return None
    return claims
""",
            )
        ],
        bugs=[
            (
                "api/tokens.py",
                'if claims.get("exp") and claims["exp"] < time.time():',
                "tokens without exp (or exp=0) are accepted forever",
            )
        ],
        decoys=[
            (
                "api/tokens.py",
                "if not hmac.compare_digest(expected, sig):",
                "constant-time comparison is correct",
            )
        ],
    )
)

# ---------------------------------------------------------------- 3 upload extension
add(
    Case(
        "hard-upload-ext",
        "Validate uploaded file types",
        "Only allow images.",
        [
            (
                "media/uploads.py",
                "",
                '''"""Store user uploads under the media root."""

from __future__ import annotations

import os
import uuid

MEDIA_ROOT = "/var/media"
ALLOWED = {"png", "jpg", "jpeg", "gif"}
MAX_BYTES = 5 * 1024 * 1024


def safe_target(user_id: int, filename: str) -> str:
    user_dir = os.path.realpath(os.path.join(MEDIA_ROOT, str(user_id)))
    target = os.path.realpath(os.path.join(user_dir, f"{uuid.uuid4().hex}-{filename}"))
    if os.path.commonpath([user_dir, target]) != user_dir:
        raise PermissionError("bad path")
    return target


def validate(filename: str, data: bytes) -> None:
    if len(data) > MAX_BYTES:
        raise ValueError("file too large")
    parts = filename.split(".")
    if len(parts) < 2 or parts[1].lower() not in ALLOWED:
        raise ValueError("file type not allowed")
''',
            )
        ],
        bugs=[
            (
                "media/uploads.py",
                "if len(parts) < 2 or parts[1].lower() not in ALLOWED:",
                "checks the second dot-segment, so photo.png.php is accepted; must use the last",
            )
        ],
        decoys=[
            (
                "media/uploads.py",
                "if os.path.commonpath([user_dir, target]) != user_dir:",
                "realpath + commonpath containment check is correct",
            )
        ],
    )
)

# ---------------------------------------------------------------- 4 tenant cache via contextvar
add(
    Case(
        "hard-tenant-cache",
        "Cache tenant settings",
        "Speed up settings lookups.",
        [
            (
                "tenancy/settings.py",
                """from contextvars import ContextVar

current_tenant: ContextVar[str] = ContextVar("current_tenant")


def get_setting(db, key: str) -> str:
    return db.fetch_setting(current_tenant.get(), key)
""",
                """from contextvars import ContextVar
from functools import lru_cache

current_tenant: ContextVar[str] = ContextVar("current_tenant")
_db = None


def configure(db) -> None:
    global _db
    _db = db
    get_setting.cache_clear()


@lru_cache(maxsize=4096)
def get_setting(key: str) -> str:
    return _db.fetch_setting(current_tenant.get(), key)
""",
            )
        ],
        bugs=[
            (
                "tenancy/settings.py",
                "@lru_cache(maxsize=4096)",
                "cache key is only `key`; tenant comes from a contextvar, so tenants share values",
            )
        ],
        decoys=[
            (
                "tenancy/settings.py",
                "get_setting.cache_clear()",
                "clearing the cache on reconfigure is correct",
            )
        ],
    )
)

# ---------------------------------------------------------------- 5 money with Decimal
add(
    Case(
        "hard-money-decimal",
        "Move invoices to Decimal",
        "Use Decimal for invoice math.",
        [
            (
                "billing/invoice.py",
                "",
                '''"""Invoice arithmetic using Decimal."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def parse_amount(amount: float) -> Decimal:
    return Decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP)


def line_total(unit: Decimal, qty: int) -> Decimal:
    return (unit * qty).quantize(CENT, rounding=ROUND_HALF_UP)


def split_evenly(total: Decimal, ways: int) -> list[Decimal]:
    if ways <= 0:
        raise ValueError("ways must be positive")
    share = (total / ways).quantize(CENT, rounding=ROUND_HALF_UP)
    return [share] * ways
''',
            )
        ],
        bugs=[
            (
                "billing/invoice.py",
                "return Decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP)",
                "Decimal(float) carries binary error (2.675 -> 2.67); parse from str",
            ),
            (
                "billing/invoice.py",
                "return [share] * ways",
                "shares do not sum to total (10.00/3 -> 3x3.33 = 9.99, or overcharges)",
            ),
        ],
        decoys=[
            (
                "billing/invoice.py",
                "return (unit * qty).quantize(CENT, rounding=ROUND_HALF_UP)",
                "Decimal * int with explicit quantize is correct",
            )
        ],
    )
)

# ---------------------------------------------------------------- 6 unbounded page size
add(
    Case(
        "hard-page-limit",
        "Add order search endpoint",
        "Search orders with paging and sort.",
        [
            (
                "api/orders.py",
                "",
                '''"""Order search handler."""

from __future__ import annotations

from typing import Any

SORTS = {"newest": "created_at DESC", "oldest": "created_at ASC", "total": "total DESC"}


def search(cur: Any, tenant_id: str, params: dict[str, str]) -> list[Any]:
    order_by = SORTS.get(params.get("sort", "newest"), SORTS["newest"])
    page = max(1, int(params.get("page", "1")))
    limit = int(params.get("limit", "50"))
    cur.execute(
        f"SELECT id, total FROM orders WHERE tenant_id = %s ORDER BY {order_by} "
        "LIMIT %s OFFSET %s",
        (tenant_id, limit, (page - 1) * limit),
    )
    rows: list[Any] = cur.fetchall()
    return rows
''',
            )
        ],
        bugs=[
            (
                "api/orders.py",
                'limit = int(params.get("limit", "50"))',
                "limit is unbounded (limit=10000000 dumps the table / DoS); negative breaks query",
            )
        ],
        decoys=[
            (
                "api/orders.py",
                'f"SELECT id, total FROM orders WHERE tenant_id = %s ORDER BY {order_by} "',
                "ORDER BY comes from a fixed mapping, not user input: not injectable",
            )
        ],
    )
)

# ---------------------------------------------------------------- 7 retrying payments
add(
    Case(
        "hard-payment-retry",
        "Retry flaky payment provider",
        "Retry charges on timeouts.",
        [
            (
                "payments/client.py",
                """import httpx


def charge(client: httpx.Client, customer: str, cents: int) -> dict:
    r = client.post("/charges", json={"customer": customer, "amount": cents})
    r.raise_for_status()
    return r.json()
""",
                """import random
import time

import httpx


def charge(client: httpx.Client, customer: str, cents: int, attempts: int = 3) -> dict:
    for i in range(attempts):
        try:
            r = client.post("/charges", json={"customer": customer, "amount": cents})
            r.raise_for_status()
            return r.json()
        except httpx.TimeoutException:
            if i == attempts - 1:
                raise
            time.sleep((2**i) + random.random())
    raise RuntimeError("unreachable")
""",
            )
        ],
        bugs=[
            (
                "payments/client.py",
                'r = client.post("/charges", json={"customer": customer, "amount": cents})',
                "retries a non-idempotent charge after a timeout with no idempotency key: double charge",
            )
        ],
        decoys=[
            (
                "payments/client.py",
                "time.sleep((2**i) + random.random())",
                "exponential backoff with jitter is correct (sync code, sleep is fine)",
            )
        ],
    )
)

# ---------------------------------------------------------------- 8 blocking call in async
add(
    Case(
        "hard-async-blocking",
        "Enrich profiles from CRM",
        "Fetch CRM data for each profile.",
        [
            (
                "profiles/enrich.py",
                "",
                '''"""Enrich user profiles with CRM data."""

from __future__ import annotations

import asyncio

import requests

CRM_URL = "https://crm.internal/api/contacts/"


async def fetch_contact(email: str) -> dict:
    resp = requests.get(CRM_URL + email, timeout=5)
    resp.raise_for_status()
    return resp.json()


async def enrich_all(emails: list[str]) -> list[dict]:
    results = await asyncio.gather(*(fetch_contact(e) for e in emails))
    await asyncio.sleep(0)
    return list(results)
''',
            )
        ],
        bugs=[
            (
                "profiles/enrich.py",
                "resp = requests.get(CRM_URL + email, timeout=5)",
                "blocking requests.get inside async def blocks the event loop; gather runs serially",
            )
        ],
        decoys=[("profiles/enrich.py", "await asyncio.sleep(0)", "harmless yield; not a bug")],
    )
)

# ---------------------------------------------------------------- 9 naive vs aware datetime
add(
    Case(
        "hard-datetime",
        "Expire stale invites",
        "Invites expire after 7 days.",
        [
            (
                "invites/expiry.py",
                "",
                '''"""Invite expiry. `created_at` values are stored timezone-aware in UTC."""

from __future__ import annotations

from datetime import datetime, timedelta

TTL = timedelta(days=7)


def expires_at(created_at: datetime) -> datetime:
    return created_at + TTL


def is_expired(created_at: datetime) -> bool:
    return datetime.now() > expires_at(created_at)
''',
            )
        ],
        bugs=[
            (
                "invites/expiry.py",
                "return datetime.now() > expires_at(created_at)",
                "comparing naive datetime.now() with an aware UTC datetime raises TypeError",
            )
        ],
        decoys=[("invites/expiry.py", "return created_at + TTL", "timedelta addition is correct")],
    )
)

# ---------------------------------------------------------------- 10 email allow-list regex
add(
    Case(
        "hard-email-regex",
        "Restrict signups to corp emails",
        "Only corp.com addresses may sign up.",
        [
            (
                "auth/signup.py",
                "",
                '''"""Signup policy."""

from __future__ import annotations

import re

CORP = re.compile(r"^[\\w.+-]+@corp\\.com")


def allowed(email: str) -> bool:
    return CORP.match(email.strip().lower()) is not None
''',
            )
        ],
        bugs=[
            (
                "auth/signup.py",
                'CORP = re.compile(r"^[\\w.+-]+@corp\\.com")',
                "regex is not anchored at the end: a@corp.com.evil.io passes the allow-list",
            )
        ],
        decoys=[
            (
                "auth/signup.py",
                "return CORP.match(email.strip().lower()) is not None",
                "normalizing case and whitespace is correct",
            )
        ],
    )
)

# ---------------------------------------------------------------- 11 unsafe yaml
add(
    Case(
        "hard-yaml-load",
        "Support YAML pipeline configs",
        "Load pipeline definitions from YAML.",
        [
            (
                "pipelines/config.py",
                """import json


def load(path: str) -> dict:
    with open(path) as f:
        return json.load(f)
""",
                """import json

import yaml


def load(path: str) -> dict:
    with open(path) as f:
        if path.endswith((".yml", ".yaml")):
            return yaml.load(f, Loader=yaml.Loader)
        return json.load(f)
""",
            )
        ],
        bugs=[
            (
                "pipelines/config.py",
                "return yaml.load(f, Loader=yaml.Loader)",
                "yaml.Loader can construct arbitrary Python objects from user configs: use safe_load",
            )
        ],
        decoys=[("pipelines/config.py", "return json.load(f)", "json.load is safe")],
    )
)

# ---------------------------------------------------------------- 12 webhook over reserialized body
add(
    Case(
        "hard-webhook-body",
        "Verify partner webhooks",
        "Check HMAC on incoming webhooks.",
        [
            (
                "hooks/partner.py",
                "",
                '''"""Partner webhook verification."""

from __future__ import annotations

import hashlib
import hmac
import json


def verify(secret: bytes, raw_body: bytes, signature: str) -> dict | None:
    payload = json.loads(raw_body)
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    expected = hmac.new(secret, canonical, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    return payload
''',
            )
        ],
        bugs=[
            (
                "hooks/partner.py",
                "expected = hmac.new(secret, canonical, hashlib.sha256).hexdigest()",
                "HMAC is computed over re-serialized JSON, not the raw body the partner signed: "
                "valid webhooks fail and duplicate keys can smuggle data",
            )
        ],
        decoys=[
            (
                "hooks/partner.py",
                "if not hmac.compare_digest(expected, signature):",
                "constant-time comparison is correct",
            )
        ],
    )
)

# ---------------------------------------------------------------- 13 batching off-by-one
add(
    Case(
        "hard-batching",
        "Batch email sends",
        "Send emails in batches of 100.",
        [
            (
                "mail/batch.py",
                "",
                '''"""Batch helper for the mail sender."""

from __future__ import annotations

from collections.abc import Iterator


def batches(items: list[str], size: int = 100) -> Iterator[list[str]]:
    if size <= 0:
        raise ValueError("size must be positive")
    for i in range(0, len(items) - size, size):
        yield items[i : i + size]
''',
            )
        ],
        bugs=[
            (
                "mail/batch.py",
                "for i in range(0, len(items) - size, size):",
                "stop bound drops the final batch (and everything when len(items) <= size)",
            )
        ],
        decoys=[("mail/batch.py", "if size <= 0:", "input validation is correct")],
    )
)

# ---------------------------------------------------------------- 14 clean but scary: subprocess + sql
add(
    Case(
        "clean-scary-subprocess",
        "Add repo archive export",
        "Export a git repo archive for a user.",
        [
            (
                "export/archive.py",
                "",
                '''"""Create git archives for export. Repo names are validated before use."""

from __future__ import annotations

import re
import subprocess

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


def archive(repo: str, ref: str, out_path: str) -> None:
    if not NAME.fullmatch(repo):
        raise ValueError("bad repo name")
    subprocess.run(
        ["git", "-C", f"/srv/repos/{repo}", "archive", "--format=zip", "-o", out_path, "--", ref],
        check=True,
        shell=False,
    )


def record(cur, user_id: int, repo: str) -> None:
    cur.execute("INSERT INTO exports (user_id, repo) VALUES (%s, %s)", (user_id, repo))
''',
            )
        ],
        decoys=[
            (
                "export/archive.py",
                "subprocess.run(",
                "list args, shell=False, validated name, `--` before ref: not command injection",
            ),
            (
                "export/archive.py",
                'cur.execute("INSERT INTO exports (user_id, repo) VALUES (%s, %s)"',
                "parameterized query: not SQL injection",
            ),
        ],
    )
)

# ---------------------------------------------------------------- 15 clean but scary: html + paths
add(
    Case(
        "clean-scary-render",
        "Render user bios",
        "Show user bios on profile pages.",
        [
            (
                "web/bio.py",
                "",
                '''"""Render user-provided bios safely."""

from __future__ import annotations

import os

from markupsafe import Markup, escape

TEMPLATES = os.path.realpath("/srv/templates")


def render_bio(bio: str) -> Markup:
    return Markup("<p>{}</p>").format(escape(bio))


def template_path(name: str) -> str:
    path = os.path.realpath(os.path.join(TEMPLATES, name))
    if os.path.commonpath([TEMPLATES, path]) != TEMPLATES:
        raise PermissionError("template outside root")
    return path
''',
            )
        ],
        decoys=[
            (
                "web/bio.py",
                'return Markup("<p>{}</p>").format(escape(bio))',
                "Markup.format escapes arguments: not XSS",
            ),
            (
                "web/bio.py",
                "if os.path.commonpath([TEMPLATES, path]) != TEMPLATES:",
                "realpath + commonpath: not path traversal",
            ),
        ],
    )
)

# ---------------------------------------------------------------- 16 clean refactor
add(
    Case(
        "clean-refactor",
        "Extract discount rules",
        "Behavior-preserving refactor.",
        [
            (
                "pricing/discounts.py",
                """def price(total: float, member: bool, coupon: str | None) -> float:
    if member:
        total = total * 0.9
    if coupon == "SAVE5":
        total = total - 5
    return max(total, 0.0)
""",
                """MEMBER_RATE = 0.9
COUPONS = {"SAVE5": 5.0}


def _member(total: float, member: bool) -> float:
    return total * MEMBER_RATE if member else total


def _coupon(total: float, coupon: str | None) -> float:
    return total - COUPONS.get(coupon or "", 0.0)


def price(total: float, member: bool, coupon: str | None) -> float:
    return max(_coupon(_member(total, member), coupon), 0.0)
""",
            )
        ],
    )
)


def diff_for(path: str, base: str, new: str) -> str:
    a = base.splitlines(True)
    b = new.splitlines(True)
    src = "/dev/null" if not base else f"a/{path}"
    body = "".join(difflib.unified_diff(a, b, src, f"b/{path}", n=3))
    mode = "new file mode 100644\n" if not base else ""
    return f"diff --git a/{path} b/{path}\n{mode}index 111..222 100644\n{body}"


def locate(new_by_path: dict[str, str], path: str, marker: str) -> int:
    hits = [i for i, ln in enumerate(new_by_path[path].splitlines(), 1) if marker in ln]
    if len(hits) != 1:
        raise ValueError(f"marker must match exactly one line in {path}: {marker!r} -> {hits}")
    return hits[0]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for c in CASES:
        d = OUT / c.id
        d.mkdir(exist_ok=True)
        new_by_path = {p: new for p, _, new in c.files}
        (d / "diff.patch").write_text("".join(diff_for(p, b, n) for p, b, n in c.files))
        (d / "meta.json").write_text(
            json.dumps({"title": c.title, "description": c.description}, indent=2)
        )

        def region(
            path: str, marker: str, desc: str, files: dict[str, str] = new_by_path
        ) -> dict[str, object]:
            ln = locate(files, path, marker)
            return {"file": path, "line_start": ln, "line_end": ln, "description": desc}

        labels = {
            "issues": [region(*b) for b in c.bugs],
            "must_not_flag": [region(*x) for x in c.decoys],
            "nitpicks": [],
        }
        (d / "labels.json").write_text(json.dumps(labels, indent=2))
    n_bugs = sum(len(c.bugs) for c in CASES)
    n_decoys = sum(len(c.decoys) for c in CASES)
    print(f"{len(CASES)} cases, {n_bugs} bugs, {n_decoys} decoys -> {OUT}")


if __name__ == "__main__":
    main()
