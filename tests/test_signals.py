import pytest
from sqlalchemy import select

from app.candles import store_rows
from app.db import Chain, Database, ScanState, Signal
from app.scanner import list_scan, scan_timeframe
from tests.conftest import DAY, END, add_coin, fixture_rows, tail_rows


def scan(db, settings, tf="1d"):
    return scan_timeframe(db, tf, settings.with_(replay_candles=100_000))


def chains(db, cid=None):
    with db.session() as s:
        q = select(Chain).order_by(Chain.id)
        if cid:
            q = q.where(Chain.coin_id == cid)
        return list(s.scalars(q))


def signals(db, cid):
    with db.session() as s:
        return list(s.scalars(select(Signal).where(Signal.coin_id == cid).order_by(Signal.candle_ts, Signal.pattern)))


def breakout_coin(db, kind="ath", n=600, base="AAA", **kw):
    rows = fixture_rows(kind, n)
    return add_coin(db, base, rows, **kw), rows


def test_breakout_opens_a_chain_with_a_fixed_entry_and_a_new_badge(db, settings):
    cid, rows = breakout_coin(db)
    scan(db, settings)
    [ch] = chains(db, cid)
    assert ch.status == "active" and ch.entry_price == rows[-1][4] and ch.entry_ts == END
    assert ch.stop < ch.entry_price < ch.target
    items = list_scan(db, "1d")["items"]
    assert [(x["base"], x["badge"], x["kind"]) for x in items] == [("AAA", "new", "chain")]
    assert items[0]["pattern"] == "ath" and items[0]["return_pct"] == 0


def test_badge_turns_from_new_to_old_when_the_next_candle_arrives(db, settings):
    cid, rows = breakout_coin(db)
    scan(db, settings)
    store_rows(db, cid, "1d", tail_rows(rows[-1], [rows[-1][4] * 1.01]))
    scan(db, settings)
    item = list_scan(db, "1d")["items"][0]
    assert item["badge"] == "old" and item["return_pct"] == pytest.approx(1.0, abs=0.01)


def test_a_rebreak_joins_the_same_chain_and_keeps_the_entry(db, settings):
    cid, rows = breakout_coin(db)
    scan(db, settings)
    entry = rows[-1][4]
    pull = tail_rows(rows[-1], [entry * f for f in (0.99, 0.985, 0.98, 0.985, 0.99, 1.0)])
    top = max(r[2] for r in rows + pull)
    again = tail_rows(pull[-1], [top * 1.03], vols=[3000.0])
    store_rows(db, cid, "1d", pull + again)
    scan(db, settings)
    [ch] = chains(db, cid)                                 # still ONE chain
    assert ch.status == "active" and ch.entry_price == entry and ch.signal_count >= 2
    assert all(s.chain_id == ch.id for s in signals(db, cid) if s.direction == "long" and s.candle_ts >= END)
    item = list_scan(db, "1d")["items"][0]
    assert item["return_pct"] > 2 and item["badge"] == "old"     # cumulative return since the FIRST entry


def test_trailing_stop_only_moves_up_and_does_rise(db, settings):
    cid, rows = breakout_coin(db)
    scan(db, settings)
    entry = rows[-1][4]
    closes = [entry * 1.01 ** j for j in range(1, 11)]
    peak = closes[-1]
    closes += [peak * f for f in (0.98, 0.96, 0.94, 0.955, 0.97, 0.985)]
    closes += [closes[-1] * 1.01 ** j for j in range(1, 11)]
    tail = tail_rows(rows[-1], closes)
    stops = []
    for r in tail:                                        # one new candle per scan, like real life
        store_rows(db, cid, "1d", [r])
        scan(db, settings)
        stops.append(chains(db, cid)[0].stop)
    assert all(b >= a for a, b in zip(stops, stops[1:]))             # never down
    assert stops[-1] > chains(db, cid)[0].initial_stop * 1.03         # and it did climb
    assert chains(db, cid)[0].status == "active"


def test_a_wick_below_the_stop_does_not_end_the_chain_but_a_close_does(db, settings):
    cid, rows = breakout_coin(db)
    scan(db, settings)
    ch = chains(db, cid)[0]
    entry, stop = ch.entry_price, ch.stop
    wick = [rows[-1][0] + DAY, entry, entry * 1.005, stop * 0.9, entry * 0.995, 1000.0]    # dips far below, closes fine
    store_rows(db, cid, "1d", [wick])
    scan(db, settings)
    assert chains(db, cid)[0].status == "active"
    crash_close = stop * 0.97
    store_rows(db, cid, "1d", [[wick[0] + DAY, wick[4], wick[4], crash_close * 0.99, crash_close, 1500.0]])
    scan(db, settings)
    ch = chains(db, cid)[0]
    assert ch.status == "stopped" and ch.exit_price == crash_close
    assert ch.result_pct == pytest.approx((crash_close - entry) / entry * 100)
    assert ch.exit_ts == wick[0] + DAY
    assert list_scan(db, "1d")["items"] == []             # off the scanner once stopped


def test_after_a_stop_out_a_new_breakout_starts_a_new_chain(db, settings):
    cid, rows = breakout_coin(db)
    scan(db, settings)
    stop = chains(db, cid)[0].stop
    crash = tail_rows(rows[-1], [stop * 0.95])
    flat = tail_rows(crash[-1], [stop * 0.96] * 5)
    top = max(r[2] for r in rows)
    reb = tail_rows(flat[-1], [top * 1.04], vols=[3000.0])
    store_rows(db, cid, "1d", crash + flat + reb)
    scan(db, settings)
    first, second = chains(db, cid)[:2]
    assert first.status == "stopped" and second.status == "active"
    assert second.entry_price != first.entry_price


def test_scans_are_idempotent(db, settings):
    cid, _ = breakout_coin(db)
    first = scan(db, settings)
    n_sig, n_chain = len(signals(db, cid)), len(chains(db, cid))
    second = scan(db, settings)
    assert first["scanned"] == 1 and second["scanned"] == 0
    assert (len(signals(db, cid)), len(chains(db, cid))) == (n_sig, n_chain)
    # forcing a re-scan from scratch must not duplicate rows either
    with db.session() as s:
        s.query(ScanState).delete()
        s.commit()
    scan(db, settings)
    assert len(signals(db, cid)) == n_sig and len(chains(db, cid)) == n_chain


def test_live_scanning_gives_the_same_result_as_replay(tmp_path, settings):
    rows = fixture_rows("ath", 600)
    entry = rows[-1][4]
    tail = tail_rows(rows[-1], [entry * f for f in (0.99, 0.985, 0.98, 0.985, 0.99, 1.0)])
    top = max(r[2] for r in rows + tail)
    tail += tail_rows(tail[-1], [top * 1.03], vols=[3000.0])
    tail += tail_rows(tail[-1], [top * 1.05, top * 1.07, top * 1.04, top * 1.02, top * 0.7])    # ends in a stop-out
    full = rows + tail

    def snapshot(d):
        with d.session() as s:
            sig = [(x.pattern, x.candle_ts, round(x.level, 6), round(x.entry, 6)) for x in
                   s.scalars(select(Signal).order_by(Signal.candle_ts, Signal.pattern))]
            ch = [(c.entry_ts, round(c.entry_price, 6), round(c.stop, 6), c.status, c.exit_ts, c.signal_count)
                  for c in s.scalars(select(Chain).order_by(Chain.entry_ts))]
        return sig, ch

    a = Database(f"sqlite:///{tmp_path}/replay.db"); a.create_all()
    b = Database(f"sqlite:///{tmp_path}/live.db"); b.create_all()
    cid_a = add_coin(a, "AAA", full)
    scan(a, settings)                                      # everything at once (replay)
    cid_b = add_coin(b, "AAA", rows)
    scan(b, settings)
    for r in tail:                                         # then one candle at a time (live)
        store_rows(b, cid_b, "1d", [r])
        scan(b, settings)
    assert snapshot(a) == snapshot(b)
    assert snapshot(a)[1][0][3] == "stopped"               # and the scenario really did end in a stop-out
    a.dispose(); b.dispose()


def test_breakdown_warning_lists_while_price_stays_below_its_level(db, settings):
    cid, rows = breakout_coin(db, "low365", 700, base="DRP")
    scan(db, settings)
    [item] = list_scan(db, "1d")["items"]
    assert (item["kind"], item["direction"], item["pattern"], item["badge"]) == ("warning", "short", "low365", "new")
    level = item["level"]
    store_rows(db, cid, "1d", tail_rows(rows[-1], [level * 1.05]))                # back above the level
    scan(db, settings)
    assert list_scan(db, "1d")["items"] == []


def test_filters_on_the_scan_list(db, settings):
    breakout_coin(db, "ath", 600, base="AAA", volume=3e7, sector="Layer-1")
    breakout_coin(db, "w", 300, base="BBB", volume=2e6, sector="DeFi")
    scan(db, settings)
    f = lambda **kw: sorted(x["base"] for x in list_scan(db, "1d", **kw)["items"])
    assert f() == ["AAA", "BBB"]
    assert f(pattern="ath") == ["AAA"] and f(pattern="w") == ["BBB"]
    assert f(tier="A") == ["AAA"] and f(tier="C") == ["BBB"]
    assert f(sector="DeFi") == ["BBB"] and f(q="aa") == ["AAA"] and f(q="zzz") == []
    assert list_scan(db, "1d", tier="A")["total"] == 2


def test_a_coin_with_too_little_history_is_skipped_not_an_error(db, settings):
    add_coin(db, "NEW", fixture_rows("ath", 600)[-10:])
    out = scan(db, settings)
    assert out["skipped"] == 1 and out["errors"] == []


def test_new_coins_pattern_names_and_levels_are_exposed(db, settings):
    breakout_coin(db, "range", 300, base="BOX")
    scan(db, settings)
    [x] = list_scan(db, "1d")["items"]
    assert x["pattern_name"] and x["level"] > 0 and x["stop"] < x["entry"] and x["tier"] == "A"
