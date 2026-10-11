"""The five pattern detectors. Pure functions: they look at candle i and earlier ones only.

A breakout counts only when the candle CLOSES beyond the level, on enough volume, and only on the first
such candle (the one before must not already have been beyond its level)."""
from __future__ import annotations

from dataclasses import dataclass

from .config import PatternParams, TF_PATTERNS
from .indicators import Indicators, Series


@dataclass
class Signal:
    pattern: str
    direction: str            # "long" | "short"
    level: float
    entry: float
    stop: float
    target: float
    volume_ratio: float
    candle_ts: int
    i: int

    @property
    def risk_pct(self) -> float:
        return abs(self.entry - self.stop) / self.entry * 100


def _volume(s: Series, ind: Indicators, i: int, p: PatternParams, require: bool):
    va = ind.volavg[i]
    ratio = (s.v[i] / va) if va else 0.0
    if require and ratio < p.volume_mult:
        return None
    return ratio


def _long(s, i, pattern, level, stop, target, ratio, p) -> Signal | None:
    entry = s.c[i]
    if not (0 < stop < entry):
        return None
    if target is None or target <= entry:
        target = entry + p.rr_target * (entry - stop)
    return Signal(pattern, "long", level, entry, stop, target, ratio, s.ts[i], i)


def detect_ath(s, ind, i, p, require_volume) -> Signal | None:
    atr = ind.atr[i]
    if i < p.ath_min_candles or not atr:
        return None
    level = ind.max_high_before[i]
    if s.c[i] <= level:
        return None
    if i >= 2 and s.c[i - 1] > ind.max_high_before[i - 1]:       # continuation, not a fresh breakout
        return None
    ratio = _volume(s, ind, i, p, require_volume)
    if ratio is None:
        return None
    return _long(s, i, "ath", level, min(level - p.stop_atr * atr, s.l[i]), None, ratio, p)


def detect_high365(s, ind, i, p, require_volume) -> Signal | None:
    atr = ind.atr[i]
    level = ind.hi_prev_long[i]
    if level is None or not atr or s.c[i] <= level:
        return None
    prev_level = ind.hi_prev_long[i - 1] if i >= 1 else None
    if prev_level is not None and s.c[i - 1] > prev_level:
        return None
    ratio = _volume(s, ind, i, p, require_volume)
    if ratio is None:
        return None
    return _long(s, i, "high365", level, min(level - p.stop_atr * atr, s.l[i]), None, ratio, p)


def detect_low365(s, ind, i, p, require_volume) -> Signal | None:
    atr = ind.atr[i]
    level = ind.lo_prev_long[i]
    if level is None or not atr or s.c[i] >= level:
        return None
    prev_level = ind.lo_prev_long[i - 1] if i >= 1 else None
    if prev_level is not None and s.c[i - 1] < prev_level:
        return None
    ratio = _volume(s, ind, i, p, require_volume)
    if ratio is None:
        return None
    entry = s.c[i]
    stop = max(level + p.stop_atr * atr, s.h[i])
    if stop <= entry:
        return None
    return Signal("low365", "short", level, entry, stop, entry - p.rr_target * (stop - entry), ratio, s.ts[i], i)


def detect_w(s, ind, i, p, require_volume) -> Signal | None:
    atr = ind.atr[i]
    if not atr or i < 2 * ind.k + 5:
        return None
    lows = ind.lows_upto(i)[-p.w_max_pivots:]
    if len(lows) < 2:
        return None
    highs = ind.highs_upto(i)
    for bi in range(len(lows) - 1, 0, -1):
        b, l2 = lows[bi]
        if i - b > p.w_max_age:
            break                                            # older second lows are older still
        for ai in range(bi - 1, -1, -1):
            a, l1 = lows[ai]
            gap = b - a
            if gap < p.w_min_gap:
                continue
            if gap > p.w_max_gap:
                break
            low = min(l1, l2)
            if abs(l2 - l1) / low > p.w_low_tol:
                continue
            necks = [price for idx, price in highs if a < idx < b]
            if not necks:
                continue
            neck = max(necks)
            if (neck - low) / low < max(p.w_min_depth, p.w_depth_atr * atr / s.c[i]):
                continue
            if min(s.l[a:i]) < low * (1 - p.w_undercut):      # a lower low breaks the shape
                continue
            if i - 1 > b and max(s.c[b + 1:i]) > neck:         # already broke out earlier
                continue
            if s.c[i] <= neck:
                continue
            ratio = _volume(s, ind, i, p, require_volume)
            if ratio is None:
                return None
            return _long(s, i, "w", neck, low - p.w_stop_atr * atr, neck + (neck - low), ratio, p)
    return None


def _touches(vals, boundary, tol, upper: bool) -> int:
    n, prev = 0, False
    for x in vals:
        hit = x >= boundary - tol if upper else x <= boundary + tol
        if hit and not prev:
            n += 1
        prev = hit
    return n


def detect_range(s, ind, i, p, require_volume) -> Signal | None:
    atr_i = ind.atr[i]
    if i < p.range_max_len + 1 or not atr_i:
        return None
    top20 = ind.hi_prev_20[i]
    if top20 is None or s.c[i] <= top20:                     # cheap test: no window can have a lower top
        return None
    atr = ind.atr[i - 1]
    if not atr:
        return None
    tol = p.range_touch_atr * atr
    for n in range(p.range_max_len, p.range_min_len - 1, -1):
        seg_h, seg_l = s.h[i - n:i], s.l[i - n:i]
        top, bot = max(seg_h), min(seg_l)
        if s.c[i] <= top or top - bot > p.range_atr_mult * atr:
            continue
        if _touches(seg_h, top, tol, True) < p.range_min_touches or _touches(seg_l, bot, tol, False) < p.range_min_touches:
            continue
        ratio = _volume(s, ind, i, p, require_volume)
        if ratio is None:
            return None
        return _long(s, i, "range", top, bot - p.range_stop_atr * atr, None, ratio, p)
    return None


DETECTORS = {"ath": detect_ath, "high365": detect_high365, "low365": detect_low365,
             "w": detect_w, "range": detect_range}


def detect_all(s: Series, ind: Indicators, i: int, tf: str, p: PatternParams, require_volume: bool = True) -> list[Signal]:
    """All signals on candle i. If an all-time-high breakout fires, the 365-day-high one is not reported."""
    out = []
    for name in TF_PATTERNS[tf]:
        sig = DETECTORS[name](s, ind, i, p, require_volume)
        if sig:
            out.append(sig)
    if any(x.pattern == "ath" for x in out):
        out = [x for x in out if x.pattern != "high365"]
    return out
