"""Market-wide views: BTC trend banner, sector heatmap, pattern statistics."""
from __future__ import annotations

from collections import defaultdict
from statistics import median

from sqlalchemy import select

from .config import PATTERN_NAMES, SECTORS, TF_SECONDS
from .db import Candle, Chain, Coin, Database, kv_get

PERIODS = {"day": 1, "week": 7, "month": 30}
MIN_COINS_PER_SECTOR = 3
SMALL_SAMPLE = 30


def _sma(vals: list[float], n: int):
    return sum(vals[-n:]) / n if len(vals) >= n else None


def btc_state(db: Database) -> dict:
    """Strong = close above both the 50- and 200-day average, Weak = below both, otherwise Mixed."""
    with db.session() as s:
        coin = s.scalar(select(Coin).where(Coin.base == "BTC"))
        closes = [] if coin is None else list(s.scalars(
            select(Candle.c).where(Candle.coin_id == coin.id, Candle.tf == "1d").order_by(Candle.ts)))
    if len(closes) < 200:
        return {"state": "Unknown", "text": "Not enough Bitcoin history yet.", "close": closes[-1] if closes else None,
                "sma50": None, "sma200": None, "change7d": None}
    c, a50, a200 = closes[-1], _sma(closes, 50), _sma(closes, 200)
    ch7 = (c / closes[-8] - 1) * 100 if len(closes) > 8 else None
    if c > a50 and c > a200:
        state, text = "Strong", "Bitcoin is above its 50-day and 200-day averages. Breakouts have a tailwind."
    elif c < a50 and c < a200:
        state, text = "Weak", "Bitcoin is below both averages. Most altcoin breakouts fail in this kind of market."
    else:
        state, text = "Mixed", "Bitcoin is between its 50-day and 200-day averages. Be selective."
    return {"state": state, "text": text, "close": c, "sma50": a50, "sma200": a200, "change7d": ch7}


def sector_heatmap(db: Database, period: str = "day") -> dict:
    n = PERIODS[period]
    secs = TF_SECONDS["1d"]
    with db.session() as s:
        latest = kv_get(s, "latest_ts:1d", 0)
        if not latest:
            return {"period": period, "latest_ts": 0, "sectors": []}
        coins = {c.id: c for c in s.scalars(select(Coin).where(Coin.active.is_(True)))}
        rows = s.execute(select(Candle.coin_id, Candle.ts, Candle.c).where(
            Candle.tf == "1d", Candle.ts.in_([latest, latest - n * secs]))).all()
    by_coin: dict[int, dict[int, float]] = defaultdict(dict)
    for cid, ts, close in rows:
        by_coin[cid][ts] = close
    groups: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for cid, got in by_coin.items():
        coin = coins.get(cid)
        if coin and latest in got and latest - n * secs in got and got[latest - n * secs] > 0:
            groups[coin.sector].append((coin.base, (got[latest] / got[latest - n * secs] - 1) * 100))
    out = []
    for sector in SECTORS:
        vals = groups.get(sector, [])
        if len(vals) < MIN_COINS_PER_SECTOR:
            continue
        best, worst = max(vals, key=lambda x: x[1]), min(vals, key=lambda x: x[1])
        out.append({"sector": sector, "avg_change": sum(v for _, v in vals) / len(vals), "count": len(vals),
                    "best": {"base": best[0], "change": best[1]}, "worst": {"base": worst[0], "change": worst[1]}})
    out.sort(key=lambda x: -x["avg_change"])
    return {"period": period, "latest_ts": latest, "sectors": out}


def pattern_stats(db: Database, tf: str = "1d") -> dict:
    """Win rate and median return of finished chains, grouped by the pattern that opened them."""
    with db.session() as s:
        rows = s.execute(select(Chain.pattern, Chain.status, Chain.result_pct, Chain.entry_ts, Chain.exit_ts)
                         .where(Chain.tf == tf)).all()
    by: dict[str, dict] = {p: {"closed": [], "active": 0, "days": []} for p in PATTERN_NAMES}
    for pat, status, res, entry_ts, exit_ts in rows:
        g = by.setdefault(pat, {"closed": [], "active": 0, "days": []})
        if status == "stopped" and res is not None:
            g["closed"].append(res)
            g["days"].append((exit_ts - entry_ts) / TF_SECONDS[tf])
        elif status == "active":
            g["active"] += 1
    out = []
    for pat, g in by.items():
        if pat == "low365":
            continue                                    # breakdown warnings have no chain
        closed = g["closed"]
        out.append({
            "pattern": pat, "pattern_name": PATTERN_NAMES.get(pat, pat), "closed": len(closed), "active": g["active"],
            "win_rate": (sum(1 for r in closed if r > 0) / len(closed) * 100) if closed else None,
            "median_return": median(closed) if closed else None,
            "median_candles": median(g["days"]) if g["days"] else None,
            "small_sample": len(closed) < SMALL_SAMPLE,
        })
    return {"tf": tf, "patterns": out,
            "note": "Closed chains only (stop hit). Past results do not predict future results."}
