import pytest

from app.db import Chain, kv_set
from app.market import btc_state, pattern_stats, sector_heatmap
from tests.conftest import DAY, END, add_coin, tail_rows


def line(n, start, end, last_ts=END):
    """n daily candles going straight from start to end, the last one at last_ts."""
    closes = [start + (end - start) * i / (n - 1) for i in range(n)]
    first = last_ts - (n - 1) * DAY
    rows, prev = [], closes[0]
    for i, c in enumerate(closes):
        rows.append([first + i * DAY, prev, max(prev, c) * 1.001, min(prev, c) * 0.999, c, 1000.0])
        prev = c
    return rows


def test_btc_state_unknown_without_history(db):
    assert btc_state(db)["state"] == "Unknown"


def test_btc_strong(db):
    add_coin(db, "BTC", line(250, 100, 200))                      # steadily up: above both averages
    assert btc_state(db)["state"] == "Strong"


def test_btc_weak(db):
    add_coin(db, "BTC", line(250, 200, 100))
    assert btc_state(db)["state"] == "Weak"


def test_btc_mixed(db):
    rows = line(250, 100, 200)
    rows += tail_rows(rows[-1], [rows[-1][4] * 0.9] * 20)           # sharp dip: below the 50-day, above the 200-day
    add_coin(db, "BTC", rows)
    st = btc_state(db)
    assert st["state"] == "Mixed" and st["sma50"] > st["close"] > st["sma200"]


def test_sector_heatmap_averages_and_minimum_coins(db):
    with db.session() as s:
        kv_set(s, "latest_ts:1d", END)
    ends = (110, 120, 130)                                          # DeFi coins, 100 -> 110 / 120 / 130 over 40 days
    for i, end in enumerate(ends):
        add_coin(db, f"D{i}", line(40, 100, end), sector="DeFi")
    for i in range(2):                                              # only two Gaming coins: left out
        add_coin(db, f"G{i}", line(40, 100, 150), sector="Gaming")
    out = sector_heatmap(db, "month")
    assert [x["sector"] for x in out["sectors"]] == ["DeFi"]
    defi = out["sectors"][0]
    # the close 30 days before the end sits 9/39 of the way along each line
    expected = sum((e / (100 + (e - 100) * 9 / 39) - 1) * 100 for e in ends) / 3
    assert defi["count"] == 3 and defi["avg_change"] == pytest.approx(expected, rel=1e-6)
    assert defi["best"]["base"] == "D2" and defi["worst"]["base"] == "D0"
    assert 0 < sector_heatmap(db, "day")["sectors"][0]["avg_change"] < 3


def test_sector_heatmap_empty_before_the_first_scan(db):
    assert sector_heatmap(db, "week") == {"period": "week", "latest_ts": 0, "sectors": []}


def test_pattern_stats(db):
    cid = add_coin(db, "AAA")
    with db.session() as s:
        for pat, res in (("ath", 10.0), ("ath", -4.0), ("ath", 6.0), ("w", 2.0)):
            s.add(Chain(coin_id=cid, tf="1d", pattern=pat, level=1, target=2, entry_price=1, entry_ts=END - 10 * DAY,
                        initial_stop=0.9, stop=0.9, status="stopped", last_ts=END, last_close=1, exit_price=1,
                        exit_ts=END - 5 * DAY, result_pct=res))
        s.add(Chain(coin_id=cid, tf="1d", pattern="range", level=1, target=2, entry_price=1, entry_ts=END,
                    initial_stop=0.9, stop=0.9, status="active", last_ts=END, last_close=1))
        s.commit()
    out = {p["pattern"]: p for p in pattern_stats(db)["patterns"]}
    assert out["ath"]["closed"] == 3 and out["ath"]["win_rate"] == pytest.approx(66.666, abs=0.01)
    assert out["ath"]["median_return"] == 6.0 and out["ath"]["small_sample"] is True
    assert out["w"]["closed"] == 1 and out["range"]["active"] == 1 and out["range"]["win_rate"] is None
    assert "low365" not in out
