"""Generate synthetic labeled PRs into eval/dataset (no network, deterministic).

Each case: base source -> new source. Diff is produced with difflib; the label line is the
line in the NEW file containing `marker` (None = clean PR with no defect).
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

OUT = Path("eval/dataset")

# (id, title, description, path, base, new, marker, bug description)
C: list[tuple[str, str, str, str, str, str, str | None, str]] = []


def case(i, title, desc, path, base, new, marker=None, bug=""):
    C.append((i, title, desc, path, base.strip("\n") + "\n", new.strip("\n") + "\n", marker, bug))


# ---------------- defects ----------------
case("bug-offbyone", "Paginate results", "Return page N of items.", "app/paging.py",
"""def page(items, n, size):
    start = (n - 1) * size
    return items[start:start + size]""",
"""def page(items, n, size):
    start = n * size
    return items[start:start + size]""",
"start = n * size", "Off-by-one: pages are 1-indexed, so page 1 skips the first page of items.")
case("bug-sqli", "Add user lookup by name", "Look up a user by name.", "app/db.py",
"""def get_user(cur, uid):
    cur.execute("SELECT * FROM users WHERE id = %s", (uid,))
    return cur.fetchone()""",
"""def get_user(cur, uid):
    cur.execute("SELECT * FROM users WHERE id = %s", (uid,))
    return cur.fetchone()


def find_by_name(cur, name):
    cur.execute(f"SELECT * FROM users WHERE name = '{name}'")
    return cur.fetchall()""",
"f\"SELECT * FROM users WHERE name", "SQL injection: user-controlled name interpolated into the query.")
case("bug-none", "Show display name", "Use nickname when set.", "app/profile.py",
"""def display(user):
    return user["name"]""",
"""def display(user):
    return user.get("nickname").strip() or user["name"]""",
"user.get(\"nickname\").strip()", "AttributeError when nickname is missing: .get returns None then .strip() is called.")
case("bug-operator", "Free shipping threshold", "Free shipping at 50 or more.", "app/shipping.py",
"""def shipping(total):
    return 5.0""",
"""def shipping(total):
    if total > 50:
        return 0.0
    return 5.0""",
"if total > 50", "Wrong operator: an order of exactly 50 should ship free (>= 50).")
case("bug-leak", "Read config file", "Load config helper.", "app/config_io.py",
"""import json


def load(path):
    with open(path) as f:
        return json.load(f)""",
"""import json


def load(path):
    f = open(path)
    return json.load(f)""",
"f = open(path)", "File handle leaked: opened without a context manager or close().")
case("bug-swallow", "Make sync resilient", "Do not crash on sync errors.", "app/sync.py",
"""def sync(client, items):
    for it in items:
        client.push(it)""",
"""def sync(client, items):
    for it in items:
        try:
            client.push(it)
        except Exception:
            pass""",
"except Exception:", "All push errors are silently swallowed so data loss goes unnoticed.")
case("bug-timing", "Verify webhook signature", "Compare signatures.", "app/webhook.py",
"""import hmac
import hashlib


def verify(secret, body, sig):
    mac = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, sig)""",
"""import hmac
import hashlib


def verify(secret, body, sig):
    mac = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return mac == sig""",
"return mac == sig", "Timing-unsafe signature comparison; must use hmac.compare_digest.")
case("bug-mutdefault", "Collect tags", "Accumulate tags.", "app/tags.py",
"""def add_tag(tag, tags=None):
    tags = tags or []
    tags.append(tag)
    return tags""",
"""def add_tag(tag, tags=[]):
    tags.append(tag)
    return tags""",
"def add_tag(tag, tags=[])", "Mutable default argument shared across calls leaks tags between callers.")
case("bug-n1", "List orders with customers", "Show customer name per order.", "app/orders.py",
"""def list_orders(db):
    return db.query("SELECT * FROM orders")""",
"""def list_orders(db):
    orders = db.query("SELECT * FROM orders")
    for o in orders:
        o["customer"] = db.query(f"SELECT name FROM customers WHERE id = {o['customer_id']}")[0]
    return orders""",
"for o in orders:", "N+1 query: one customer query per order in a loop (should JOIN).")
case("bug-secret", "Add API client", "Client for billing API.", "app/billing.py",
"""import os


def headers():
    return {"Authorization": "Bearer " + os.environ["BILLING_KEY"]}""",
"""import os


def headers():
    return {"Authorization": "Bearer sk_live_51Hq83LkjD92mXzP0aQvT7yRw"}""",
"sk_live_", "Hardcoded live API secret committed to source.")
case("bug-traversal", "Serve uploaded files", "Download endpoint helper.", "app/files.py",
"""import os

ROOT = "/srv/uploads"


def path_for(name):
    return os.path.join(ROOT, os.path.basename(name))""",
"""import os

ROOT = "/srv/uploads"


def path_for(name):
    return os.path.join(ROOT, name)""",
"os.path.join(ROOT, name)", "Path traversal: name like ../../etc/passwd escapes ROOT (basename removed).")
case("bug-race", "Increment counter", "Thread-safe counter.", "app/counter.py",
"""import threading

_lock = threading.Lock()
count = 0


def inc():
    global count
    with _lock:
        count += 1""",
"""import threading

_lock = threading.Lock()
count = 0


def inc():
    global count
    count += 1""",
"    count += 1", "Race condition: lock removed so concurrent increments are lost.")
case("bug-divzero", "Average latency", "Compute mean latency.", "app/stats.py",
"""def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0""",
"""def mean(xs):
    return sum(xs) / len(xs)""",
"return sum(xs) / len(xs)", "ZeroDivisionError on empty input; the empty guard was removed.")
case("bug-units", "Convert timeout", "Timeout is configured in seconds.", "app/timeouts.py",
"""def to_ms(seconds):
    return seconds * 1000""",
"""def to_ms(seconds):
    return seconds * 100""",
"seconds * 100", "Wrong unit factor: seconds to ms must multiply by 1000.")
case("bug-auth", "Admin check refactor", "Simplify admin check.", "app/auth/perms.py",
"""def is_admin(user):
    return user.get("role") == "admin" and user.get("verified") is True""",
"""def is_admin(user):
    return user.get("role") == "admin" or user.get("verified") is True""",
"or user.get(\"verified\")", "Auth bypass: `or` lets any verified user be admin (should be `and`).")
case("bug-cache", "Cache user lookups", "Add cache.", "app/cache.py",
"""_cache = {}


def get(key, loader):
    if key not in _cache:
        _cache[key] = loader(key)
    return _cache[key]""",
"""_cache = {}


def get(key, loader):
    if key not in _cache:
        _cache[key] = loader(key)
    return _cache[key]


def invalidate(user_id):
    _cache.pop(str(user_id), None)""",
"_cache.pop(str(user_id)", "Cache keys are stored as given (ints), but invalidate pops str(user_id), so entries never evict.")
case("bug-regexdos", "Validate email", "Stricter email check.", "app/validate.py",
"""import re


def ok(email):
    return "@" in email""",
"""import re


def ok(email):
    return re.match(r"^([a-zA-Z0-9]+)*@example\\.com$", email) is not None""",
"re.match(", "Catastrophic backtracking regex (nested quantifier) allows ReDoS.")
case("bug-retry", "Add retry helper", "Retry failed calls 3 times.", "app/retry.py",
"""def call(fn):
    return fn()""",
"""def call(fn):
    for attempt in range(3):
        try:
            return fn()
        except Exception:
            if attempt == 2:
                return None""",
"return None", "After the last retry the error is swallowed and None returned instead of re-raising.")
case("bug-asyncblock", "Fetch with delay", "Wait before fetching.", "app/fetch.py",
"""import asyncio


async def fetch(client, url):
    await asyncio.sleep(0.1)
    return await client.get(url)""",
"""import asyncio
import time


async def fetch(client, url):
    time.sleep(0.1)
    return await client.get(url)""",
"time.sleep(0.1)", "Blocking time.sleep inside an async function stalls the event loop.")
case("bug-dedupe", "Dedupe ids", "Remove duplicate ids keeping order.", "app/ids.py",
"""def uniq(ids):
    return ids""",
"""def uniq(ids):
    return list(set(ids))""",
"list(set(ids))", "set() does not preserve order, contradicting the keep-order requirement.")

# ---------------- clean / benign ----------------
case("ok-docs", "Fix README typo", "Docs only.", "README.md",
"""# Project
Usage: run it.
A tool.""",
"""# Project
Usage: run it.
A tool for pricing.""")
case("ok-rename", "Rename helper", "Rename variable for clarity.", "app/util.py",
"""def total(xs):
    s = 0
    for x in xs:
        s += x
    return s""",
"""def total(xs):
    acc = 0
    for x in xs:
        acc += x
    return acc""")
case("ok-docstring", "Add docstring", "Document function.", "app/greet.py",
"""def greet(name):
    return "hello " + name""",
'''def greet(name):
    """Return a greeting for `name`."""
    return "hello " + name''')
case("ok-const", "Extract constant", "Name the magic number.", "app/limits.py",
"""def too_big(n):
    return n > 1000""",
"""MAX_ITEMS = 1000


def too_big(n):
    return n > MAX_ITEMS""")
case("ok-test", "Add tests for add()", "Tests only.", "tests/test_math.py",
"""def test_one():
    assert 1 + 1 == 2""",
"""def test_one():
    assert 1 + 1 == 2


def test_two():
    assert 2 + 2 == 4""")
case("ok-refactor", "Use comprehension", "Behavior-preserving refactor.", "app/evens.py",
"""def evens(xs):
    out = []
    for x in xs:
        if x % 2 == 0:
            out.append(x)
    return out""",
"""def evens(xs):
    return [x for x in xs if x % 2 == 0]""")
case("ok-typehints", "Add type hints", "Typing only.", "app/add.py",
"""def add(a, b):
    return a + b""",
"""def add(a: int, b: int) -> int:
    return a + b""")
case("ok-logging", "Log startup", "Add info log.", "app/main.py",
"""import logging

log = logging.getLogger(__name__)


def start():
    return True""",
"""import logging

log = logging.getLogger(__name__)


def start():
    log.info("starting")
    return True""")
case("ok-deps", "Bump requests", "Dependency bump.", "requirements.txt",
"""requests==2.31.0
pydantic==2.5.0""",
"""requests==2.32.3
pydantic==2.5.0""")
case("ok-fix-good", "Correctly guard empty list", "Fix crash on empty input.", "app/first.py",
"""def first(xs):
    return xs[0]""",
"""def first(xs):
    return xs[0] if xs else None""")


def diff_for(path: str, base: str, new: str) -> str:
    body = "".join(difflib.unified_diff(base.splitlines(True), new.splitlines(True),
                                         f"a/{path}", f"b/{path}", n=3))
    return f"diff --git a/{path} b/{path}\nindex 111..222 100644\n{body}"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for i, title, desc, path, base, new, marker, bug in C:
        d = OUT / i
        d.mkdir(exist_ok=True)
        (d / "diff.patch").write_text(diff_for(path, base, new))
        (d / "meta.json").write_text(json.dumps({"title": title, "description": desc}, indent=2))
        issues = []
        if marker:
            lines = new.splitlines()
            ln = next(n for n, t in enumerate(lines, 1) if marker in t)
            issues = [{"file": path, "line_start": ln, "line_end": ln, "description": bug}]
        (d / "labels.json").write_text(json.dumps({"issues": issues, "nitpicks": []}, indent=2))
    print(len(C), "cases,", sum(1 for c in C if c[6]), "with defects")


if __name__ == "__main__":
    main()
