"""Read-only queries behind the coin detail page."""
from __future__ import annotations

from sqlalchemy import func, select

from .config import PATTERN_NAMES, TF_SECONDS
from .db import Candle, Chain, Coin, Database, ScanState, Signal, kv_get
from .universe import load_names

_NAMES: dict[str, str] | None = None


def coin_name(base: str) -> str:
    global _NAMES
    if _NAMES is None:
        _NAMES = load_names()
    return _NAMES.get(base, base)


def coin_public(c: Coin) -> dict:
    return {"base": c.base, "name": coin_name(c.base), "symbol": c.symbol, "venue": c.venue, "tier": c.tier,
            "sector": c.sector, "quote_volume": c.quote_volume, "last_price": c.last_price, "active": c.active,
            "note": c.note}


def _chain_dict(ch: Chain, latest: int) -> dict:
    secs = TF_SECONDS[ch.tf]
    ret = ((ch.exit_price if ch.status == "stopped" else ch.last_close) - ch.entry_price) / ch.entry_price * 100
    return {"id": ch.id, "tf": ch.tf, "status": ch.status, "pattern": ch.pattern,
            "pattern_name": PATTERN_NAMES.get(ch.pattern, ch.pattern), "level": ch.level, "target": ch.target,
            "entry_price": ch.entry_price, "entry_ts": ch.entry_ts, "initial_stop": ch.initial_stop, "stop": ch.stop,
            "last_close": ch.last_close, "return_pct": ret, "age_candles": int(((ch.exit_ts or ch.last_ts) - ch.entry_ts) // secs),
            "signal_count": ch.signal_count, "exit_price": ch.exit_price, "exit_ts": ch.exit_ts,
            "badge": "new" if ch.status == "active" and ch.entry_ts >= latest else "old"}


def _signal_dict(sg: Signal) -> dict:
    risk = abs(sg.entry - sg.stop) / sg.entry * 100
    reward = abs(sg.target - sg.entry) / sg.entry * 100
    return {"id": sg.id, "tf": sg.tf, "pattern": sg.pattern, "pattern_name": PATTERN_NAMES.get(sg.pattern, sg.pattern),
            "direction": sg.direction, "level": sg.level, "entry": sg.entry, "stop": sg.stop, "target": sg.target,
            "volume_ratio": sg.volume_ratio, "candle_ts": sg.candle_ts, "chain_id": sg.chain_id,
            "risk_pct": risk, "reward_pct": reward, "rr": (reward / risk) if risk else None}


def find_coin(db: Database, base: str) -> Coin | None:
    with db.session() as s:
        return s.scalar(select(Coin).where(Coin.base == base.upper()))


def coin_detail(db: Database, coin: Coin, tfs: tuple[str, ...]) -> dict:
    out = {"coin": coin_public(coin), "timeframes": {}}
    with db.session() as s:
        for tf in tfs:
            latest = kv_get(s, f"latest_ts:{tf}", 0)
            n, first, last = s.execute(select(func.count(), func.min(Candle.ts), func.max(Candle.ts))
                                       .where(Candle.coin_id == coin.id, Candle.tf == tf)).one()
            ch = s.scalar(select(Chain).where(Chain.coin_id == coin.id, Chain.tf == tf, Chain.status == "active"))
            sig = s.scalar(select(Signal).where(Signal.coin_id == coin.id, Signal.tf == tf)
                           .order_by(Signal.candle_ts.desc(), Signal.id.desc()).limit(1))
            warn = None
            short = s.scalar(select(Signal).where(Signal.coin_id == coin.id, Signal.tf == tf, Signal.direction == "short")
                             .order_by(Signal.candle_ts.desc()).limit(1))
            st = s.get(ScanState, (coin.id, tf))
            if short is not None and st is not None and st.last_close < short.level:
                warn = {**_signal_dict(short), "badge": "new" if short.candle_ts >= latest else "old"}
            out["timeframes"][tf] = {
                "candles": n, "first_ts": first, "last_ts": last,
                "short_history": tf == "1d" and n < 365,
                "chain": _chain_dict(ch, latest) if ch else None,
                "latest_signal": _signal_dict(sig) if sig else None,
                "warning": warn,
            }
    return out


def coin_signals(db: Database, coin: Coin, tf: str, limit: int = 100) -> dict:
    with db.session() as s:
        latest = kv_get(s, f"latest_ts:{tf}", 0)
        sigs = list(s.scalars(select(Signal).where(Signal.coin_id == coin.id, Signal.tf == tf)
                              .order_by(Signal.candle_ts.desc(), Signal.id.desc()).limit(limit)))
        chains = list(s.scalars(select(Chain).where(Chain.coin_id == coin.id, Chain.tf == tf)
                                .order_by(Chain.entry_ts.desc()).limit(50)))
    return {"tf": tf, "signals": [_signal_dict(x) for x in sigs], "chains": [_chain_dict(c, latest) for c in chains]}


def coin_candles(db: Database, coin: Coin, tf: str, limit: int) -> list[list]:
    with db.session() as s:
        rows = s.execute(select(Candle.ts, Candle.o, Candle.h, Candle.l, Candle.c, Candle.v)
                         .where(Candle.coin_id == coin.id, Candle.tf == tf)
                         .order_by(Candle.ts.desc()).limit(limit)).all()
    return [list(r) for r in reversed(rows)]
