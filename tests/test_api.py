import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.demo import DemoExchange
from app.main import RateLimiter, create_app
from app.scheduler import Pipeline
from tests.conftest import Clock


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """One demo build shared by the read-only API tests."""
    tmp = tmp_path_factory.mktemp("api")
    st = Settings(data_source="demo", database_url=f"sqlite:///{tmp}/api.db", universe_size=100, retry_max_s=0,
                  disable_scheduler=True, rate_limit_per_min=100_000, demo_coins=120, demo_days=800)
    clock = Clock()
    demo = DemoExchange(n_coins=120, days=800, clock=clock)
    from app.db import Database
    d = Database(st.database_url)
    d.create_all()
    asyncio.run(Pipeline(d, demo, st, clock=clock, sleep=lambda s: asyncio.sleep(0), sectors=dict(demo.sectors)).run_due())
    d.dispose()
    return st, demo, clock


@pytest.fixture()
def client(built):
    st, demo, clock = built
    with TestClient(create_app(st, api=demo, clock=clock)) as c:
        yield c


def test_meta(client):
    m = client.get("/api/meta").json()
    assert m["timeframes"] == ["1d"] and m["data_source"] == "demo" and "Layer-1" in m["sectors"]
    assert set(m["patterns"]) == {"ath", "high365", "low365", "w", "range"} and "not financial advice" in m["disclaimer"]


def test_health(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["universe"]["coins"] >= 98 and h["universe"]["goal_met"] is False
    assert h["timeframes"]["1d"]["last_scan"]["scanned"] > 50


def test_scan_list_and_filters(client):
    all_ = client.get("/api/scan").json()
    assert all_["tf"] == "1d" and all_["count"] > 0 and all_["latest_ts"] > 0
    row = next(x for x in all_["items"] if x["base"] == "NOVA")
    assert row["pattern"] == "ath" and row["badge"] == "new" and row["tier"] in "ABC" and row["stop"] < row["entry"]
    only_w = client.get("/api/scan", params={"pattern": "w"}).json()
    assert only_w["count"] > 0 and all("w" in [x["pattern"], *x["also_ids"]] for x in only_w["items"])
    assert all(x["tier"] == "A" for x in client.get("/api/scan", params={"tier": "A"}).json()["items"])
    assert [x["base"] for x in client.get("/api/scan", params={"q": "nov"}).json()["items"]] == ["NOVA"]
    breakdown = client.get("/api/scan", params={"pattern": "low365"}).json()["items"]
    assert breakdown and all(x["kind"] == "warning" and x["direction"] == "short" for x in breakdown)


@pytest.mark.parametrize("params", [
    {"tf": "5m"}, {"tf": "4h"}, {"pattern": "nope"}, {"tier": "Z"}, {"sector": "Moon"},
    {"q": "bad value!"}, {"q": "x" * 40},
])
def test_bad_scan_input_is_a_short_422_never_a_500(client, params):
    r = client.get("/api/scan", params=params)
    assert r.status_code == 422 and len(r.json()["detail"]) < 200


def test_coin_detail(client):
    d = client.get("/api/coins/nova").json()
    assert d["coin"]["base"] == "NOVA" and d["coin"]["tier"] in "ABC" and d["coin"]["venue"]
    tf = d["timeframes"]["1d"]
    assert tf["candles"] == 800 and tf["short_history"] is False
    assert tf["chain"]["pattern"] == "ath" and tf["chain"]["status"] == "active" and tf["chain"]["badge"] == "new"
    assert tf["latest_signal"]["pattern_name"] and tf["latest_signal"]["rr"] > 1
    btc = client.get("/api/coins/BTC").json()
    assert btc["coin"]["name"] == "Bitcoin"                                  # name comes from sectors.json


def test_watchlist_endpoint(client):
    r = client.get("/api/watchlist", params={"bases": "nova,btc,NOPE,nova"}).json()
    assert [x["coin"]["base"] for x in r["items"]] == ["NOVA", "BTC"] and r["missing"] == ["NOPE"]
    assert r["items"][0]["timeframes"]["1d"]["chain"]["pattern"] == "ath"
    assert client.get("/api/watchlist").json() == {"items": [], "missing": []}
    assert client.get("/api/watchlist", params={"bases": "a b"}).status_code == 422
    assert client.get("/api/watchlist", params={"bases": "A," * 400}).status_code == 422


def test_breakdown_coin_has_a_warning_not_a_chain(client):
    tf = client.get("/api/coins/DRIP").json()["timeframes"]["1d"]
    assert tf["warning"]["pattern"] == "low365" and tf["warning"]["direction"] == "short"


@pytest.mark.parametrize("path", ["/api/coins/NOPE", "/api/coins/..%2Fetc", "/api/coins/" + "A" * 40,
                                  "/api/coins/NOPE/candles", "/api/signals/NOPE"])
def test_unknown_or_malformed_coin_is_404(client, path):
    assert client.get(path).status_code == 404


def test_candles(client):
    r = client.get("/api/coins/ETH/candles", params={"limit": 120}).json()
    rows = r["candles"]
    assert len(rows) == 120 and rows == sorted(rows, key=lambda x: x[0]) and r["secs"] == 86400
    assert all(len(x) == 6 and x[2] >= max(x[1], x[4]) - 1e-9 for x in rows)
    assert client.get("/api/coins/ETH/candles", params={"limit": 5}).status_code == 422
    assert client.get("/api/coins/ETH/candles", params={"limit": 99999}).status_code == 422


def test_signals_for_a_coin(client):
    r = client.get("/api/signals/NOVA").json()
    assert r["signals"] and r["chains"] and r["chains"][0]["status"] == "active"
    assert r["signals"][0]["candle_ts"] >= r["signals"][-1]["candle_ts"]


def test_market_sectors_and_stats(client):
    m = client.get("/api/market").json()
    assert m["state"] in ("Strong", "Mixed", "Weak") and m["sma200"] and m["text"]
    for period in ("day", "week", "month"):
        s = client.get("/api/sectors", params={"period": period}).json()
        assert s["period"] == period and s["sectors"] and all(x["count"] >= 3 for x in s["sectors"])
    assert client.get("/api/sectors", params={"period": "year"}).status_code == 422
    st = client.get("/api/stats/patterns").json()
    assert {p["pattern"] for p in st["patterns"]} == {"ath", "high365", "w", "range"} and "predict" in st["note"]


def test_security_headers_and_no_store(client):
    r = client.get("/api/meta")
    assert "default-src 'self'" in r.headers["content-security-policy"] and "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"


def test_rate_limit(built):
    st, demo, clock = built
    app = create_app(st.with_(rate_limit_per_min=5), api=demo, clock=clock)
    with TestClient(app) as c:
        codes = [c.get("/api/meta").status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]


def test_rate_limiter_window_and_cleanup():
    rl = RateLimiter(3)
    assert [rl.check("a", now=t) for t in (0, 1, 2, 3)] == [True, True, True, False]
    assert rl.check("b", now=3) and rl.check("a", now=61)           # window slides; other visitors unaffected
    rl.check("c", now=1000)
    assert "a" not in rl.hits and "b" not in rl.hits                # idle visitors are forgotten


def test_admin_route_does_not_exist_without_a_token(client):
    assert client.post("/api/admin/rescan").status_code in (404, 405)


def test_admin_route_needs_the_right_token(built):
    st, demo, clock = built
    app = create_app(st.with_(admin_token="s3cret-token"), api=demo, clock=clock)
    with TestClient(app) as c:
        assert c.post("/api/admin/rescan").status_code == 403
        assert c.post("/api/admin/rescan", headers={"X-Admin-Token": "wrong"}).status_code == 403
        assert c.post("/api/admin/rescan", headers={"X-Admin-Token": "s3cret-token"}).status_code == 202
        assert c.post("/api/admin/rescan", params={"tf": "4h"}, headers={"X-Admin-Token": "s3cret-token"}).status_code == 422


def test_api_never_exposes_files_or_docs(client):
    for path in ("/docs", "/openapi.json", "/static/../app/config.py", "/static/nope.js"):
        assert client.get(path).status_code in (404, 422)
