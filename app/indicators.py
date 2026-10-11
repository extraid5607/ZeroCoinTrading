"""Indicators. Every value at index i uses only candles 0..i, never later ones (no look-ahead)."""
from __future__ import annotations

from bisect import bisect_right
from collections import deque
from dataclasses import dataclass


@dataclass
class Series:
    ts: list
    o: list
    h: list
    l: list
    c: list
    v: list

    @classmethod
    def from_rows(cls, rows) -> "Series":
        return cls([int(r[0]) for r in rows], [r[1] for r in rows], [r[2] for r in rows],
                   [r[3] for r in rows], [r[4] for r in rows], [r[5] for r in rows])

    def __len__(self) -> int:
        return len(self.ts)


def _rolling_prev(vals, n, fn_max: bool):
    """out[i] = max (or min) of vals[i-n:i] (the n candles BEFORE i), None until n candles exist."""
    out = [None] * len(vals)
    dq: deque = deque()                     # indexes, values monotonic
    for i in range(len(vals)):
        if i >= n:
            out[i] = vals[dq[0]]
        while dq and (vals[dq[-1]] <= vals[i] if fn_max else vals[dq[-1]] >= vals[i]):
            dq.pop()
        dq.append(i)
        while dq and dq[0] <= i - n:
            dq.popleft()
    return out


class Indicators:
    def __init__(self, s: Series, k: int, long_window: int = 365, atr_period: int = 14, vol_period: int = 20):
        n = len(s)
        self.n, self.k = n, k
        # ATR (Wilder)
        tr = [s.h[0] - s.l[0]] + [max(s.h[i] - s.l[i], abs(s.h[i] - s.c[i - 1]), abs(s.l[i] - s.c[i - 1]))
                                  for i in range(1, n)]
        self.atr: list = [None] * n
        if n >= atr_period:
            a = sum(tr[:atr_period]) / atr_period
            self.atr[atr_period - 1] = a
            for i in range(atr_period, n):
                a = (a * (atr_period - 1) + tr[i]) / atr_period
                self.atr[i] = a
        # average volume of the previous 20 candles
        self.volavg: list = [None] * n
        run = 0.0
        for i in range(n):
            if i >= vol_period:
                self.volavg[i] = run / vol_period
                run -= s.v[i - vol_period]
            run += s.v[i]
        # highest high before i (all history) and rolling windows
        self.max_high_before: list = [None] * n
        m = None
        for i in range(n):
            self.max_high_before[i] = m
            m = s.h[i] if m is None else max(m, s.h[i])
        self.hi_prev_long = _rolling_prev(s.h, long_window, True)
        self.lo_prev_long = _rolling_prev(s.l, long_window, False)
        self.hi_prev_20 = _rolling_prev(s.h, 20, True)
        # swing points, usable only once k later candles have confirmed them
        self.p_highs: list[tuple[int, float]] = []
        self.p_lows: list[tuple[int, float]] = []
        for j in range(k, n - k):
            wl, wr = s.h[j - k:j], s.h[j + 1:j + k + 1]
            if s.h[j] > max(wl) and s.h[j] >= max(wr):
                self.p_highs.append((j, s.h[j]))
            wl, wr = s.l[j - k:j], s.l[j + 1:j + k + 1]
            if s.l[j] < min(wl) and s.l[j] <= min(wr):
                self.p_lows.append((j, s.l[j]))
        self._hconf = [j + k for j, _ in self.p_highs]
        self._lconf = [j + k for j, _ in self.p_lows]

    def highs_upto(self, i: int) -> list[tuple[int, float]]:
        """Swing highs that are confirmed by candle i."""
        return self.p_highs[:bisect_right(self._hconf, i)]

    def lows_upto(self, i: int) -> list[tuple[int, float]]:
        return self.p_lows[:bisect_right(self._lconf, i)]
