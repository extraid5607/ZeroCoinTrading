import asyncio

import pytest
from sqlalchemy import func, select

from app import candles as cm
from app.candles import fill_gaps, find_gaps, is_valid, update_all, update_coin
from app.db import Candle, Coin
from app.exchange import CcxtExchange
from app.universe import refresh_universe
from tests.conftest import DAY, END, add_coin


def run(coro):
    return asyncio.run(coro)


def count(db, cid, tf="1d"):
    with db.session() as s:
        return s.scalar(select(func.count()).select_from(Candle).where(Candle.coin_id == cid, Candle.tf == tf))


def span(db, cid, tf="1d"):
    with db.session() as s:
        return s.execute(select(func.min(Candle.ts), func.max(Candle.ts)).where(Candle.coin_id == cid, Candle.tf == tf)).one()


def get_coin(db, base):
    with db.session() as s:
        return s.scalar(select(Coin).where(Coin.base == base))


@pytest.fixture()
def coin(db, demo, settings):
    run(refresh_universe(db, demo, settings, sectors={}))
    return get_coin(db, "ETH")


def test_backfill_stores_completed_candles_only(db, demo, settings, coin, clock):
    res = run(update_coin(db, demo, coin, "1d", settings, clock(), {}))
    lo, hi = span(db, coin.id)
    assert hi == END                                    # the candle still forming is NOT stored
    assert res.stored == count(db, coin.id) == 800


def test_incremental_update_fetches_only_new_candles(db, demo, settings, coin, clock):
    run(update_coin(db, demo, coin, "1d", settings, clock(), {}))
    again = run(update_coin(db, demo, coin, "1d", settings, clock(), {}))
    assert again.stored == 0
    clock.advance(DAY)
    nxt = run(update_coin(db, demo, coin, "1d", settings, clock(), {}))
    assert nxt.stored == 1 and span(db, coin.id)[1] == END + DAY


def test_backfill_resumes_after_a_crash(db, demo, settings, coin, clock, monkeypatch):
    monkeypatch.setattr(cm, "PAGE", 300)
    real, calls = demo.ohlcv, {"n": 0}

    async def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("connection reset")
        return await real(*a, **k)

    demo.ohlcv = flaky
    with pytest.raises(RuntimeError):
        run(update_coin(db, demo, coin, "1d", settings, clock(), {}))
    partial = count(db, coin.id)
    assert 0 < partial < 800                              # first page was kept
    first_before = span(db, coin.id)[0]
    demo.ohlcv = real
    run(update_coin(db, demo, coin, "1d", settings, clock(), {}))
    assert count(db, coin.id) == 800 and span(db, coin.id)[0] == first_before   # no restart, no duplicates


def test_one_failing_coin_does_not_stop_the_others(db, demo, settings, clock):
    run(refresh_universe(db, demo, settings, sectors={}))
    with db.session() as s:
        coins = list(s.scalars(select(Coin).where(Coin.base.in_(["BTC", "ETH", "SOL"]))))
    real = demo.ohlcv

    async def maybe(venue, symbol, *a, **k):
        if symbol == "ETH/USDT":
            raise RuntimeError("boom")
        return await real(venue, symbol, *a, **k)

    demo.ohlcv = maybe
    out = run(update_all(db, demo, coins, "1d", settings, clock(), {}))
    assert set(out["errors"]) == {"ETH"} and out["stored"] == 1600


class Fixed:
    """Exchange returning a fixed list of rows."""
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    async def ohlcv(self, venue, symbol, tf, since_ts, limit=1000):
        self.calls += 1
        return [list(r) for r in self.rows if since_ts is None or r[0] >= since_ts][:limit]

    async def close(self):
        pass


def good_rows(n=10, start=END - 20 * DAY, price=10.0):
    return [[start + i * DAY, price, price * 1.01, price * 0.99, price, 100.0] for i in range(n)]


def test_invalid_candles_are_rejected(db, settings, clock):
    cid = add_coin(db, "BAD")
    rows = good_rows(5)
    rows[1][2] = 5.0                       # high below the close
    rows[2][3] = -1.0                      # negative low
    rows[3][4] = float("nan")
    with db.session() as s:
        coin = s.get(Coin, cid)
    res = run(update_coin(db, Fixed(rows), coin, "1d", settings, clock(), {}))
    assert res.invalid == 3 and res.stored == 2


def test_is_valid():
    assert is_valid([0, 10, 11, 9, 10, 5])
    assert not is_valid([0, 10, 9, 9, 10, 5])         # high below open
    assert not is_valid([0, 10, 11, 10.5, 10, 5])     # low above close
    assert not is_valid([0, 0, 1, 0, 1, 5])           # zero price
    assert not is_valid([0, 10, 11, 9, 10, -1])       # negative volume
    assert not is_valid([0, 10, float("inf"), 9, 10, 1])


def test_outlier_candle_is_held_until_a_second_fetch_agrees(db, settings, clock):
    cid = add_coin(db, "ODD")
    rows = good_rows(6)
    rows[4] = [rows[4][0], 10.0, 40.0, 9.9, 10.0, 100.0]        # a wick 4x the neighbouring close
    ex, seen = Fixed(rows), {}
    with db.session() as s:
        coin = s.get(Coin, cid)
    r1 = run(update_coin(db, ex, coin, "1d", settings, clock(), seen))
    assert r1.held_outlier and count(db, cid) == 4              # candles before it stored, it and later ones wait
    r2 = run(update_coin(db, ex, coin, "1d", settings, clock(), seen))
    assert not r2.held_outlier and count(db, cid) == 6          # the same values came back: accepted


def test_a_changing_outlier_is_not_accepted(db, settings, clock):
    cid = add_coin(db, "ODD2")
    rows = good_rows(6)
    rows[4] = [rows[4][0], 10.0, 40.0, 9.9, 10.0, 100.0]
    ex, seen = Fixed(rows), {}
    with db.session() as s:
        coin = s.get(Coin, cid)
    run(update_coin(db, ex, coin, "1d", settings, clock(), seen))
    ex.rows[4] = [rows[4][0], 10.0, 30.0, 9.9, 10.0, 100.0]     # second fetch disagrees
    r = run(update_coin(db, ex, coin, "1d", settings, clock(), seen))
    assert r.held_outlier and count(db, cid) == 4


def test_4h_retention_keeps_only_the_newest_candles(db, demo, settings, coin, clock):
    st = settings.with_(retention={"1d": 0, "4h": 1200, "1h": 1000})
    run(update_coin(db, demo, coin, "4h", st, clock(), {}))
    assert count(db, coin.id, "4h") == 1200
    clock.advance(4 * 3600 * 3)
    run(update_coin(db, demo, coin, "4h", st, clock(), {}))
    assert count(db, coin.id, "4h") == 1200                      # trimmed back, newest kept
    assert span(db, coin.id, "4h")[1] == int(clock() // 14400) * 14400 - 14400


def test_gaps_are_found_refilled_and_not_asked_for_twice(db, demo, settings, coin, clock):
    run(update_coin(db, demo, coin, "1d", settings, clock(), {}))
    with db.session() as s:
        victim = END - 100 * DAY
        s.query(Candle).filter(Candle.coin_id == coin.id, Candle.tf == "1d",
                               Candle.ts.between(victim, victim + 4 * DAY)).delete()
        s.commit()
    assert len(find_gaps(db, coin.id, "1d")) == 1
    assert run(fill_gaps(db, demo, coin, "1d", clock())) == 5
    assert find_gaps(db, coin.id, "1d") == []
    # a hole the exchange cannot fill is tried once, then remembered
    with db.session() as s:
        s.query(Candle).filter(Candle.coin_id == coin.id, Candle.tf == "1d", Candle.ts == END - 50 * DAY).delete()
        s.commit()
    ex = Fixed([])
    run(fill_gaps(db, ex, coin, "1d", clock()))
    run(fill_gaps(db, ex, coin, "1d", clock()))
    assert ex.calls == 1


# ---- real exchange layer: rate limit backoff --------------------------------------------------
class FakeCcxtClient:
    def __init__(self, failures):
        self.markets = {"BTC/USDT": {}}
        self.failures = failures
        self.calls = 0

    async def fetch_ohlcv(self, symbol, tf, since, limit):
        import ccxt
        self.calls += 1
        if self.calls <= self.failures:
            raise ccxt.RateLimitExceeded("429")
        return [[1_700_000_000_000, 1, 2, 0.5, 1.5, 10]]

    async def close(self):
        pass


def test_rate_limit_backs_off_with_growing_waits():
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    client = FakeCcxtClient(failures=2)
    ex = CcxtExchange(client_factory=lambda v: client, sleep=fake_sleep)
    rows = run(ex.ohlcv("binance", "BTC/USDT", "1d", None))
    assert rows == [[1_700_000_000.0, 1, 2, 0.5, 1.5, 10]]
    assert sleeps == [2.0, 5.0]


def test_rate_limit_gives_up_after_the_last_retry():
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    ex = CcxtExchange(client_factory=lambda v: FakeCcxtClient(failures=99), sleep=fake_sleep)
    with pytest.raises(RuntimeError, match="rate limited"):
        run(ex.ohlcv("binance", "BTC/USDT", "1d", None))
    assert sleeps == [2.0, 5.0, 12.0, 30.0]
