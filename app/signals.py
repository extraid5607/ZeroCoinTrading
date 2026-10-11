"""Turns detector output into stored signals and breakout chains.

advance() is used for BOTH the live scan and the history replay, so they cannot disagree: it walks
candles in order, and for each one (1) checks/trails the open chain, then (2) looks for new signals.

Breakout chain: the first long signal on a coin and timeframe opens a chain with a fixed entry price.
Later signals while the chain is alive are linked to it (entry stays). The stop only ever moves up
(to the latest confirmed swing low minus half an ATR). The chain closes when a candle CLOSES below the
stop, and the result is the move from the first entry to that close.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import PatternParams
from .db import Chain, Signal
from .indicators import Indicators, Series
from .patterns import detect_all


@dataclass
class AdvanceResult:
    candles: int = 0
    signals: int = 0
    chains_opened: int = 0
    chains_closed: int = 0


def _trail(chain: Chain, s: Series, ind: Indicators, i: int, p: PatternParams) -> None:
    entry_i = bisect_left(s.ts, chain.entry_ts)
    atr = ind.atr[i]
    lows = [(j, pr) for j, pr in ind.lows_upto(i) if j >= entry_i]
    if lows and atr:
        new_stop = lows[-1][1] - p.chain_stop_atr * atr
        if new_stop > chain.stop:                   # never moves down
            chain.stop = new_stop


def advance(session: Session, coin_id: int, tf: str, s: Series, ind: Indicators, start_i: int,
            p: PatternParams, require_volume: bool = True) -> AdvanceResult:
    res = AdvanceResult()
    n = len(s)
    start_i = max(1, start_i)
    if start_i >= n:
        return res
    chain = session.scalar(select(Chain).where(Chain.coin_id == coin_id, Chain.tf == tf, Chain.status == "active"))
    existing = {(pat, ts) for pat, ts in session.execute(
        select(Signal.pattern, Signal.candle_ts).where(Signal.coin_id == coin_id, Signal.tf == tf,
                                                      Signal.candle_ts >= s.ts[start_i]))}
    for i in range(start_i, n):
        res.candles += 1
        # 1. the open chain
        if chain is not None and chain.status == "active":
            if s.c[i] < chain.stop:                       # closed below the stop: the chain ends
                chain.status = "stopped"
                chain.exit_price = s.c[i]
                chain.exit_ts = s.ts[i]
                chain.result_pct = (s.c[i] - chain.entry_price) / chain.entry_price * 100
                chain.last_ts, chain.last_close = s.ts[i], s.c[i]
                res.chains_closed += 1
                chain = None
            else:
                _trail(chain, s, ind, i, p)
                chain.last_ts, chain.last_close = s.ts[i], s.c[i]
        # 2. new signals on this candle
        for sig in detect_all(s, ind, i, tf, p, require_volume):
            if (sig.pattern, sig.candle_ts) in existing:
                continue
            row = Signal(coin_id=coin_id, tf=tf, pattern=sig.pattern, direction=sig.direction, level=sig.level,
                         entry=sig.entry, stop=sig.stop, target=sig.target, volume_ratio=sig.volume_ratio,
                         candle_ts=sig.candle_ts)
            if sig.direction == "long":
                if chain is None:
                    chain = Chain(coin_id=coin_id, tf=tf, pattern=sig.pattern, level=sig.level, target=sig.target,
                                  entry_price=sig.entry, entry_ts=sig.candle_ts, initial_stop=sig.stop,
                                  stop=sig.stop, status="active", last_ts=sig.candle_ts, last_close=sig.entry,
                                  volume_ratio=sig.volume_ratio, signal_count=1)
                    session.add(chain)
                    session.flush()
                    res.chains_opened += 1
                elif chain.entry_ts != sig.candle_ts:      # a re-break: same chain, entry unchanged
                    chain.signal_count += 1
                row.chain_id = chain.id
            session.add(row)
            existing.add((sig.pattern, sig.candle_ts))
            res.signals += 1
    session.flush()
    return res
