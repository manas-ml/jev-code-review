from examples.svc import cache, exports, limiter, money, sessions


def test_limiter_allows_then_blocks():
    lim = limiter.Limiter(capacity=2, refill_per_sec=0.0)
    assert lim.allow("a") and lim.allow("a") and not lim.allow("a")


def test_session_roundtrip():
    assert sessions.verify(sessions.sign({"uid": 1}))["uid"] == 1


def test_export_path_inside_tenant_dir():
    assert exports.export_path("acme", "a.csv") == "/srv/exports/acme/a.csv"


def test_cache_loads_once():
    calls = []
    c = cache.ProfileCache(lambda t, u: calls.append(u) or {"u": u})
    c.get("t1", 1)
    c.get("t1", 1)
    assert calls == [1]


def test_money_format():
    assert money.from_cents(1999) == "19.99"
