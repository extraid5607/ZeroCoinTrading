import random

import pytest

from app.config import PatternParams
from app.demo import random_walk
from app.indicators import Indicators, Series
from app.patterns import detect_all
from tests.conftest import DAY, END, fixture_rows

P = PatternParams()


def run(rows, tf="1d", k=3, require_volume=True, i=None, params=P):
    s = Series.from_rows(rows)
    ind = Indicators(s, k)
    i = len(s) - 1 if i is None else i
    return {x.pattern: x for x in detect_all(s, ind, i, tf, params, require_volume)}


SIZES = {"ath": 600, "high365": 800, "low365": 700, "w": 300, "range": 300}


@pytest.mark.parametrize("kind", list(SIZES))
@pytest.mark.parametrize("seed", range(5))
def test_pattern_fires_on_its_fixture(kind, seed):
    found = run(fixture_rows(kind, SIZES[kind], seed))
    assert kind in found, f"{kind} not detected (seed {seed}): {list(found)}"
    sig = found[kind]
    assert sig.volume_ratio >= 1.5
    if sig.direction == "long":
        assert sig.stop < sig.entry < sig.target
    else:
        assert sig.target < sig.entry < sig.stop


def test_ath_wins_over_high365():
    found = run(fixture_rows("ath", 600))
    assert "ath" in found and "high365" not in found


def test_high365_is_not_an_all_time_high():
    found = run(fixture_rows("high365", 800))
    assert "high365" in found and "ath" not in found


# ---- near misses: each must NOT trigger -------------------------------------------------------
def test_wick_above_level_but_close_below_does_not_count():
    rows = fixture_rows("ath", 600)
    level = max(r[2] for r in rows[:-1])
    last = rows[-1]
    last[4] = level * 0.99                      # closes below the old high ...
    last[2] = level * 1.05                      # ... after poking above it
    assert "ath" not in run(rows)


@pytest.mark.parametrize("kind", ["ath", "range", "w"])
def test_low_volume_breakout_is_ignored_unless_volume_rule_is_off(kind):
    rows = fixture_rows(kind, SIZES[kind])
    rows[-1][5] = 1000.0                        # same as the average: ratio about 1
    assert kind not in run(rows)
    assert kind in run(rows, require_volume=False)


def test_ath_needs_180_candles_of_history():
    assert "ath" in run(fixture_rows("ath", 600))
    assert "ath" not in run(fixture_rows("ath", 150))


def test_365_patterns_need_a_year_of_history():
    assert "low365" not in run(fixture_rows("low365", 700)[-300:])
    assert "high365" not in run(fixture_rows("high365", 800)[-300:])


def test_continuation_candle_is_not_a_new_signal():
    rows = fixture_rows("ath", 600)
    last = rows[-1]
    nxt = [last[0] + DAY, last[4], last[4] * 1.05, last[4] * 0.99, last[4] * 1.04, 3000.0]
    # the day after a breakout that closes even higher: same move, no second fresh signal
    assert "ath" not in run(rows + [nxt])


def test_w_with_lows_too_far_apart_is_rejected():
    for seed in range(5):
        assert "w" in run(fixture_rows("w", 300, seed))
        assert "w" not in run(fixture_rows("w", 300, seed, second_low=1.12))     # 12% apart, limit is 3%


def test_w_second_low_too_close_to_first():
    rows = fixture_rows("w", 300)
    # compress the timeline: only 5 candles between the lows is not a double bottom
    s = Series.from_rows(rows)
    ind = Indicators(s, 3)
    lows = ind.lows_upto(len(s) - 1)
    assert any(b - a >= 10 for (a, _), (b, _) in zip(lows, lows[1:]))
    tight = PatternParams(w_min_gap=40)
    assert "w" not in run(rows, params=tight)


def test_range_that_is_too_wide_is_rejected():
    rows = fixture_rows("range", 300)
    assert "range" in run(rows)
    tight = PatternParams(range_atr_mult=1.0)
    assert "range" not in run(rows, params=tight)


def test_range_needs_touches_on_both_sides():
    rows = fixture_rows("range", 300)
    strict = PatternParams(range_min_touches=40)
    assert "range" not in run(rows, params=strict)


def test_intraday_timeframes_only_run_w_and_range():
    rows = fixture_rows("ath", 600)
    assert "ath" not in run(rows, tf="4h", k=4) and "high365" not in run(rows, tf="1h", k=4)
    assert "w" in run(fixture_rows("w", 300), tf="4h", k=4)


# ---- no look-ahead ---------------------------------------------------------------------------------
def signature(rows, i, k=3, tf="1d"):
    s = Series.from_rows(rows)
    ind = Indicators(s, k)
    return sorted((x.pattern, round(x.level, 9), round(x.entry, 9), round(x.stop, 9), round(x.target, 9))
                  for x in detect_all(s, ind, i, tf, P, require_volume=False))


@pytest.mark.parametrize("kind", list(SIZES))
def test_results_do_not_depend_on_later_candles(kind):
    """Judging candle i must give the same answer whether or not the candles after i exist."""
    rows = fixture_rows(kind, SIZES[kind])
    future = random_walk(60, END + 60 * DAY, DAY, random.Random(5), rows[-1][4], 0.04)
    for r in future:
        r[0] = r[0] - 0           # timestamps already follow on
    longer = rows + [[rows[-1][0] + (j + 1) * DAY] + r[1:] for j, r in enumerate(future)]
    start = len(rows) - 40
    for i in range(start, len(rows)):
        assert signature(rows[: i + 1], i) == signature(longer, i), f"look-ahead at candle {i}"


def test_random_history_has_no_look_ahead_either():
    rng = random.Random(3)
    rows = random_walk(700, END, DAY, rng, 50.0, 0.05)
    extra = random_walk(30, END + 30 * DAY, DAY, random.Random(4), rows[-1][4], 0.05)
    longer = rows + [[rows[-1][0] + (j + 1) * DAY] + r[1:] for j, r in enumerate(extra)]
    for i in range(380, 700, 7):
        assert signature(rows[: i + 1], i) == signature(longer, i)
