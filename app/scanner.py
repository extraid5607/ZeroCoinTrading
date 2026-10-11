"""Runs the pattern engine over every active coin for one timeframe, and reads the results back."""
from __future__ import annotations

import logging
import time
from bisect import bisect_right

from sqlalchemy import select

from .config import PATTERN_NAMES, PIVOT_K, Settings
from .db import Candle, Chain, Coin, Database, ScanRun, ScanState, Signal, kv_get, kv_set
from .indicators import Indicators, Series
from .queries import coin_name
from .signals import advance

log = logging.getLogger("scanner")
MIN_CANDLES = 30


def load_series(s, coin_id: int, tf: str) -> Series:
    rows = s.execute(select(Candle.ts, Candle.o, Candle.h, Candle.l, Candle.c, Candle.v)
                     .where(Candle.coin_id == coin_id, Candle.tf == tf).order_by(Candle.ts)).all()
    return Series.from_rows(rows)


def scan_coin(session, coin: Coin, tf: str, settings: Settings) -> tuple[str, int]:
    """Returns (outcome, signals). outcome: scanned | skipped | current."""
    s = load_series(session, coin.id, tf)
    if len(s) < MIN_CANDLES:
        return "skipped", 0
    state = session.get(ScanState, (coin.id, tf))
    if state is not None and state.last_ts >= s.ts[-1]:
        return "current", 0
    ind = Indicators(s, PIVOT_K[tf])
    if state is None:
        start_i = max(1, len(s) - settings.replay_candles)       # first scan = replay of recent history
    else:
        start_i = bisect_right(s.ts, state.last_ts)
    res = advance(session, coin.id, tf, s, ind, start_i, settings.params, settings.require_volume)
    if state is None:
        state = ScanState(coin_id=coin.id, tf=tf, last_ts=s.ts[-1], last_close=s.c[-1])
        session.add(state)
    else:
        state.last_ts, state.last_close = s.ts[-1], s.c[-1]
    session.commit()
    return "scanned", res.signals


def scan_timeframe(db: Database, tf: str, settings: Settings, now: float | None = None, missing: int = 0) -> dict:
    started = now if now is not None else time.time()
    run = ScanRun(tf=tf, started=time.time(), missing=missing)
    scanned = skipped = signals = 0
    errors: list[str] = []
    with db.session() as s:
        coins = list(s.scalars(select(Coin).where(Coin.active.is_(True))))
    for coin in coins:
        with db.session() as s:
            try:
                outcome, n = scan_coin(s, coin, tf, settings)
            except Exception as exc:                   # noqa: BLE001 - one coin must not stop the scan
                s.rollback()
                errors.append(f"{coin.base}: {type(exc).__name__}: {exc}"[:120])
                log.exception("scan failed for %s %s", coin.base, tf)
                continue
        if outcome == "scanned":
            scanned += 1
            signals += n
        elif outcome == "skipped":
            skipped += 1
    with db.session() as s:
        latest = max((st.last_ts for st in s.scalars(select(ScanState).where(ScanState.tf == tf))), default=0)
        run.finished = time.time()
        run.candle_ts = latest
        run.coins_scanned, run.coins_skipped, run.signals_created = scanned, skipped, signals
        run.errors = "; ".join(errors[:20])
        s.add(run)
        s.commit()
        kv_set(s, f"latest_ts:{tf}", latest)
    log.info("scan %s: %d scanned, %d skipped, %d signals, %d errors", tf, scanned, skipped, signals, len(errors))
    return {"tf": tf, "scanned": scanned, "skipped": skipped, "signals": signals, "errors": errors,
            "candle_ts": latest}


# ---- reading results ---------------------------------------------------------------------------
def _row(coin: Coin, kind: str, *, pattern: str, direction: str, also: list | None = None, badge: str, level: float, entry: float,
         stop: float, target: float | None = None, ret_pct: float, entry_ts: int, last_ts: int, count: int, ratio: float, secs: int) -> dict:
    return {
        "base": coin.base, "name": coin_name(coin.base), "symbol": coin.symbol, "venue": coin.venue, "tier": coin.tier, "sector": coin.sector,
        "pattern": pattern, "pattern_name": PATTERN_NAMES.get(pattern, pattern), "direction": direction,
        "also": [PATTERN_NAMES.get(p, p) for p in (also or [])], "also_ids": list(also or []),
        "kind": kind, "badge": badge, "level": level, "entry": entry, "stop": stop, "target": target,
        "return_pct": ret_pct, "entry_ts": entry_ts, "age_candles": int((last_ts - entry_ts) // secs),
        "signals": count, "volume_ratio": ratio,
    }


def list_scan(db: Database, tf: str, pattern: str | None = None, tier: str | None = None,
              sector: str | None = None, q: str | None = None, limit: int = 600) -> dict:
    from .config import TF_SECONDS
    secs = TF_SECONDS[tf]
    with db.session() as s:
        latest = kv_get(s, f"latest_ts:{tf}", 0)
        items: list[dict] = []
        chains = s.execute(select(Chain, Coin).join(Coin, Coin.id == Chain.coin_id)
                           .where(Chain.tf == tf, Chain.status == "active", Coin.active.is_(True))).all()
        entry_patterns: dict[int, list[str]] = {}
        if chains:
            ids = [ch.id for ch, _ in chains]
            for cid, pat, ts in s.execute(select(Signal.chain_id, Signal.pattern, Signal.candle_ts)
                                          .where(Signal.chain_id.in_(ids)).order_by(Signal.id)):
                entry_patterns.setdefault(cid, []).append((pat, ts))
        for ch, coin in chains:
            chain_pats = list(dict.fromkeys(p for p, ts in entry_patterns.get(ch.id, [])))
            display_pattern = ch.pattern
            if "ath" in chain_pats:
                display_pattern = "ath"
            elif "high365" in chain_pats and ch.pattern not in ("ath",):
                display_pattern = "high365"
            also_patterns = [p for p in chain_pats if p != display_pattern]
            items.append(_row(coin, "chain", pattern=display_pattern, also=also_patterns, direction="long",
                              badge="new" if ch.entry_ts >= latest else "old", level=ch.level,
                              entry=ch.entry_price, stop=ch.stop, target=ch.target,
                              ret_pct=(ch.last_close - ch.entry_price) / ch.entry_price * 100,
                              entry_ts=ch.entry_ts, last_ts=ch.last_ts, count=ch.signal_count,
                              ratio=ch.volume_ratio, secs=secs))
        # breakdown warnings: latest 365-day-low signal per coin, listed while the price stays below its level
        shorts = s.execute(select(Signal, Coin, ScanState).join(Coin, Coin.id == Signal.coin_id)
                           .join(ScanState, (ScanState.coin_id == Signal.coin_id) & (ScanState.tf == Signal.tf))
                           .where(Signal.tf == tf, Signal.direction == "short", Coin.active.is_(True))
                           .order_by(Signal.candle_ts.desc())).all()
        seen = set()
        for sig, coin, st in shorts:
            if coin.id in seen:
                continue
            seen.add(coin.id)
            if st.last_close < sig.level:
                items.append(_row(coin, "warning", pattern=sig.pattern, direction="short",
                                  badge="new" if sig.candle_ts >= latest else "old", level=sig.level,
                                  entry=sig.entry, stop=sig.stop, target=sig.target,
                                  ret_pct=(st.last_close - sig.entry) / sig.entry * 100,
                                  entry_ts=sig.candle_ts, last_ts=st.last_ts, count=1,
                                  ratio=sig.volume_ratio, secs=secs))
    total = len(items)
    if pattern:
        items = [x for x in items if x["pattern"] == pattern or pattern in x["also_ids"]]
    if tier:
        items = [x for x in items if x["tier"] == tier]
    if sector:
        items = [x for x in items if x["sector"] == sector]
    if q:
        ql = q.strip().upper()
        items = [x for x in items if ql in x["base"]]
    items.sort(key=lambda x: (x["badge"] != "new", x["tier"], x["base"]))
    return {"tf": tf, "latest_ts": latest, "total": total, "count": len(items), "items": items[:limit]}
