import asyncio

import pytest
from sqlalchemy import select

from app.db import Candle, Chain, Coin, ScanState, kv_get
from app.scanner import list_scan
from app.scheduler import Pipeline
from tests.conftest import DAY, END, T0


def run(coro):
    return asyncio.run(coro)


def make(db, demo, settings, clock, **kw):
    return Pipeline(db, demo, settings, clock=clock, sleep=kw.pop("sleep", None) or _nosleep, sectors={})


async def _nosleep(s):
    return None


def bases(db):
    with db.session() as s:
        return set(s.scalars(select(Coin.base).where(Coin.active.is_(True))))


def last_ts(db, base, tf="1d"):
    with db.session() as s:
        cid = s.scalar(select(Coin.id).where(Coin.base == base))
        return s.scalar(select(ScanState.last_ts).where(ScanState.coin_id == cid, ScanState.tf == tf))


def test_first_start_builds_everything(db, demo, settings, clock):
    p = make(db, demo, settings, clock)
    assert run(p.run_due()) == ["universe", "1d", "gaps"]
    got = bases(db)
    assert len(got) >= 98 and "USDX" not in got                    # price rule removed the unlisted stablecoin
    assert not got & {"USDC", "BTCUP", "WBTC"}
    items = list_scan(db, "1d")["items"]
    seen = {x["pattern"] for x in items} | {a for x in items for a in x["also_ids"]}
    assert seen == {"ath", "high365", "low365", "w", "range"}      # every pattern shows up in the demo data
    assert {x["base"] for x in items} >= {"NOVA", "KRIO", "DRIP", "WAVE", "BOXY"}
    assert all(x["badge"] == "new" for x in items if x["base"] in {"NOVA", "KRIO", "DRIP", "WAVE", "BOXY"})
    assert run(p.run_due()) == []                                  # nothing more is due


def test_next_day_turns_new_badges_old_and_adds_one_candle(db, demo, settings, clock):
    p = make(db, demo, settings, clock)
    run(p.run_due())
    clock.advance(DAY + 400)                                       # 00:06 UTC the next day
    assert run(p.run_due()) == ["universe", "1d", "gaps"]
    assert last_ts(db, "BTC") == END + DAY
    nova = next(x for x in list_scan(db, "1d")["items"] if x["base"] == "NOVA")
    assert nova["badge"] == "old"


def test_missed_days_are_caught_up_in_order(db, demo, settings, clock):
    p = make(db, demo, settings, clock)
    run(p.run_due())
    before = list_scan(db, "1d")["count"]
    clock.advance(4 * DAY)
    run(p.run_due())
    assert last_ts(db, "BTC") == END + 4 * DAY
    with db.session() as s:
        n = s.scalar(select(Candle.ts).where(Candle.tf == "1d").order_by(Candle.ts.desc()).limit(1))
    assert n == END + 4 * DAY and before > 0


def test_a_scan_that_ran_before_the_candle_existed_is_repeated_when_it_is_due(db, demo, settings, clock):
    clock.t = END + DAY + 60                    # one minute after the newest daily candle closed
    p = make(db, demo, settings, clock)
    real = demo.ohlcv

    async def late(venue, symbol, tf, since, limit=1000):         # the exchange has not published it yet
        return [r for r in await real(venue, symbol, tf, since, limit) if r[0] < END]

    demo.ohlcv = late
    run(p.run_due())
    assert last_ts(db, "BTC") == END - DAY
    assert run(p.run_due()) == []               # still inside the 5 minute delay: not repeated yet
    demo.ohlcv = real
    clock.advance(600)                          # 11 minutes after the close
    assert run(p.run_due()) == ["1d"]
    assert last_ts(db, "BTC") == END


def test_waits_for_late_candles_then_scans(db, demo, settings, clock):
    st = settings.with_(retry_interval_s=120, retry_max_s=1800)
    p0 = make(db, demo, st, clock)
    run(p0.run_due())
    clock.t = END + 2 * DAY + 360              # 00:06 UTC: 6 minutes after the new candle closed
    real, state, sleeps = demo.ohlcv, {"released": False}, []

    async def withheld(venue, symbol, tf, since, limit=1000):
        rows = await real(venue, symbol, tf, since, limit)
        if symbol == "ETH/USDT" and not state["released"]:
            rows = [r for r in rows if r[0] < END + DAY]
        return rows

    async def fake_sleep(sec):
        sleeps.append(sec)
        clock.advance(sec)
        if len(sleeps) == 2:
            state["released"] = True

    demo.ohlcv = withheld
    p = Pipeline(db, demo, st, clock=clock, sleep=fake_sleep, sectors={})
    run(p.run_due())
    assert sleeps == [120, 120]
    assert last_ts(db, "ETH") == END + DAY
    assert p.health()["timeframes"]["1d"]["missing_latest_candle"] == 0


def test_gives_up_waiting_and_reports_who_was_missing(db, demo, settings, clock):
    st = settings.with_(retry_interval_s=120, retry_max_s=1800)
    run(make(db, demo, st, clock).run_due())
    clock.t = END + 2 * DAY + 360
    real, sleeps = demo.ohlcv, []

    async def never(venue, symbol, tf, since, limit=1000):
        rows = await real(venue, symbol, tf, since, limit)
        return [r for r in rows if r[0] < END + DAY] if symbol == "ETH/USDT" else rows

    async def fake_sleep(sec):
        sleeps.append(sec)
        clock.advance(sec)

    demo.ohlcv = never
    p = Pipeline(db, demo, st, clock=clock, sleep=fake_sleep, sectors={})
    run(p.run_due())
    assert 10 <= len(sleeps) <= 15                      # waited about 30 minutes, not forever
    h = p.health()["timeframes"]["1d"]
    assert h["missing_latest_candle"] == 1 and h["missing_examples"] == ["ETH"]
    assert last_ts(db, "BTC") == END + DAY               # everyone else was still scanned


def test_no_waiting_when_started_hours_after_the_candle_closed(db, demo, settings, clock):
    st = settings.with_(retry_interval_s=120, retry_max_s=1800)
    run(make(db, demo, st, clock).run_due())
    clock.advance(DAY + 3 * 3600)
    real, sleeps = demo.ohlcv, []

    async def never(venue, symbol, tf, since, limit=1000):
        rows = await real(venue, symbol, tf, since, limit)
        return [r for r in rows if r[0] < END + DAY] if symbol == "ETH/USDT" else rows

    async def fake_sleep(sec):
        sleeps.append(sec)

    demo.ohlcv = never
    run(Pipeline(db, demo, st, clock=clock, sleep=fake_sleep, sectors={}).run_due())
    assert sleeps == []


def test_a_failing_exchange_does_not_stop_the_first_build(db, demo, settings, clock):
    demo.fail_venues = {"okx"}
    p = make(db, demo, settings, clock)
    run(p.run_due())
    h = p.health()
    assert "okx" in h["venue_errors"] and len(bases(db)) > 50
    assert h["universe"]["coins"] == len(bases(db))


def test_health_report_fields(db, demo, settings, clock):
    p = make(db, demo, settings, clock)
    run(p.run_due())
    h = p.health()
    assert h["data_source"] == "demo"
    assert set(h["universe"]) >= {"coins", "wanted", "markets_seen", "removed", "by_venue", "tiers", "venues_ok"}
    tf = h["timeframes"]["1d"]
    assert tf["latest_candle_ts"] == END and tf["last_scan"]["scanned"] > 50
    assert tf["backfill"]["coins_with_candles"] >= 98 and tf["stale_coins"] == 0


def test_4h_runs_on_its_own_schedule_and_only_uses_w_and_range(db, demo, settings, clock):
    st = settings.with_(enabled_timeframes=("1d", "4h"))
    p = make(db, demo, st, clock)
    assert run(p.run_due()) == ["universe", "1d", "4h", "gaps"]
    items = list_scan(db, "4h")["items"]
    assert items and {x["pattern"] for x in items} <= {"w", "range"}
    assert run(p.run_due()) == []
    clock.advance(4 * 3600 + 400)
    assert run(p.run_due()) == ["4h"]                  # not the daily scan, not the universe


def test_run_forever_survives_a_crash(db, demo, settings, clock):
    p = make(db, demo, settings, clock)
    calls = {"n": 0}
    real = p.run_due

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return await real()

    p.run_due = flaky

    async def go():
        t = asyncio.create_task(p.run_forever(every=0.01))
        await asyncio.sleep(0.6)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t

    run(go())
    assert calls["n"] >= 2 and len(bases(db)) > 50
