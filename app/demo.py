"""Offline fake exchange used for DATA_SOURCE=demo and for tests.

It makes about 640 fake coins with seeded random-walk history, a few coins whose latest candle is a
deliberate pattern breakout, and a set of junk (stablecoins, leveraged tokens, wrapped coins) that the
universe filters must remove. Same interface as the real exchange layer, so the whole pipeline
(universe -> candles -> patterns -> chains) runs exactly as it does live.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field

from .config import SECTORS, TF_SECONDS
from .exchange import MarketInfo, Row

REAL_TICKERS = (
    "BTC ETH BNB SOL XRP DOGE ADA TRX AVAX LINK DOT TON SHIB LTC BCH UNI ATOM XLM ETC NEAR APT ARB OP "
    "FIL INJ HBAR VET AAVE MKR ICP IMX RENDER FET GRT SUI SEI TIA STX LDO SAND MANA AXS GALA PEPE WIF "
    "BONK FLOKI CAKE CRV COMP SNX RUNE KAVA ALGO EGLD FLOW XTZ EOS THETA ZEC DASH JUP SYRUP"
).split()

# base -> (kind of injected latest candle)
INJECTED = {
    "NOVA": "ath", "ORBX": "ath", "PRYM": "ath",
    "KRIO": "high365", "LUMA": "high365", "VEXA": "high365",
    "DRIP": "low365", "FALL": "low365",
    "WAVE": "w", "TIDE": "w", "ZETA": "w",
    "BOXY": "range", "SLAB": "range", "PADX": "range",
}
JUNK = {
    "USDC": "stable", "FDUSD": "stable", "TUSD": "stable", "DAI": "stable",
    "USDX": "stable",                      # not on the stablecoin list: caught by the price rule
    "BTCUP": "random", "BTCDOWN": "random", "ETHUP": "random", "ETHDOWN": "random",
    "WBTC": "random", "STETH": "random", "CBBTC": "random",
}
DEMO_SECTOR_OF = {
    "BTC": "Layer-1", "ETH": "Layer-1", "SOL": "Layer-1", "ADA": "Layer-1", "AVAX": "Layer-1",
    "DOGE": "Meme", "SHIB": "Meme", "PEPE": "Meme", "WIF": "Meme", "BONK": "Meme", "FLOKI": "Meme",
    "BNB": "Exchange", "LINK": "Infrastructure", "GRT": "Infrastructure", "FIL": "Infrastructure",
    "UNI": "DeFi", "AAVE": "DeFi", "MKR": "DeFi", "CRV": "DeFi", "COMP": "DeFi", "LDO": "DeFi",
    "ARB": "Layer-2", "OP": "Layer-2", "IMX": "Layer-2", "STX": "Layer-2",
    "RENDER": "AI", "FET": "AI", "SAND": "Gaming", "MANA": "Gaming", "AXS": "Gaming", "GALA": "Gaming",
}


# ------------------------------------------------------------------------------ series builders
def _wobble(rng: random.Random, vbase: float) -> float:
    return vbase * max(0.5, 1 + 0.12 * rng.gauss(0, 1))


def _bar(rng, o, c, wick, vol) -> list:
    hi = max(o, c) * (1 + rng.random() * wick)
    lo = min(o, c) * (1 - rng.random() * wick)
    return [o, hi, lo, c, vol]


def _rows_from_closes(closes, end_ts, secs, rng, wick, vbase) -> list[Row]:
    n = len(closes)
    rows = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        o_, h, l, c_, v = _bar(rng, o, c, wick, _wobble(rng, vbase))
        rows.append([end_ts - (n - 1 - i) * secs, o_, h, l, c_, v])
    return rows


def _interp(points, n) -> list[float]:
    pts = sorted(points)
    out = []
    for i in range(n):
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= i <= x1:
                t = (i - x0) / (x1 - x0) if x1 > x0 else 0
                out.append(y0 + (y1 - y0) * t)
                break
        else:
            out.append(pts[0][1] if i < pts[0][0] else pts[-1][1])
    return out


def _noisy(path, rng, noise) -> list[float]:
    return [p * math.exp(max(-3, min(3, rng.gauss(0, 1))) * noise) for p in path]


def _replace(rows, i, o, c, rng, wick, vol) -> None:
    _o, h, l, _c, v = _bar(rng, o, c, wick, vol)
    rows[i] = [rows[i][0], o, h, l, c, v]


def random_walk(n, end_ts, secs, rng, price, sigma_daily, vbase=1000.0) -> list[Row]:
    sig = sigma_daily * math.sqrt(secs / 86_400)
    closes = []
    p = price
    for _ in range(n):
        p *= math.exp(rng.gauss(0, sig) - 0.5 * sig * sig)
        closes.append(p)
    return _rows_from_closes(closes, end_ts, secs, rng, 0.35 * sig * 4, vbase)


def stable_series(n, end_ts, secs, rng, vbase=1000.0) -> list[Row]:
    closes = [1 + max(-0.004, min(0.004, rng.gauss(0, 0.0007))) for _ in range(n)]
    return _rows_from_closes(closes, end_ts, secs, rng, 0.0004, vbase)


def pattern_series(kind, n, end_ts, secs, rng, price=100.0, vbase=1000.0, breakout_volume=3.0,
                   second_low=1.01) -> list[Row]:
    """History whose LAST candle is a clean breakout of the given kind.

    ath      new all-time high            high365  new 365-candle high, below the old all-time high
    low365   new 365-candle low           w        double bottom, last candle closes above the neckline
    range    flat box, last candle closes above the top
    """
    P = price
    last, prev = n - 1, n - 2
    wick = 0.006
    if kind == "ath":
        closes = _noisy(_interp([(0, 0.3 * P), (n - 3, P)], n), rng, 0.015)
        rows = _rows_from_closes(closes, end_ts, secs, rng, wick, vbase)
        top = max(r[2] for r in rows[:prev])
        _replace(rows, prev, rows[prev - 1][4], top * 0.96, rng, wick, vbase)
        level = max(r[2] for r in rows[:last])
        _replace(rows, last, rows[prev][4], level * 1.03, rng, 0.003, vbase * breakout_volume)
    elif kind == "high365":
        pts = [(0, 0.5 * P), (100, 3 * P), (220, P), (n - 240, 1.4 * P), (prev, 1.38 * P)]
        rows = _rows_from_closes(_noisy(_interp(pts, n), rng, 0.012), end_ts, secs, rng, wick, vbase)
        top = max(r[2] for r in rows[prev - 365:prev])
        _replace(rows, prev, rows[prev - 1][4], top * 0.96, rng, wick, vbase)
        level = max(r[2] for r in rows[last - 365:last])
        _replace(rows, last, rows[prev][4], level * 1.03, rng, 0.003, vbase * breakout_volume)
    elif kind == "low365":
        rows = _rows_from_closes(_noisy(_interp([(0, 3 * P), (prev, 0.8 * P)], n), rng, 0.012),
                                 end_ts, secs, rng, wick, vbase)
        bot = min(r[3] for r in rows[prev - 365:prev])
        _replace(rows, prev, rows[prev - 1][4], bot * 1.04, rng, wick, vbase)
        level = min(r[3] for r in rows[last - 365:last])
        _replace(rows, last, rows[prev][4], level * 0.97, rng, 0.003, vbase * breakout_volume)
    elif kind == "w":
        b = last - 15
        a = b - 30
        pk = (a + b) // 2
        pts = [(0, 1.4 * P), (a, P), (pk, 1.19 * P), (b, second_low * P), (prev, 1.16 * P)]
        rows = _rows_from_closes(_noisy(_interp(pts, n), rng, 0.003), end_ts, secs, rng, 0.004, vbase)
        neck = max(r[2] for r in rows[a + 1:b])
        _replace(rows, last, rows[prev][4], neck * 1.02, rng, 0.003, vbase * breakout_volume)
    elif kind == "range":
        s = last - 45
        closes = _interp([(0, 1.5 * P), (s, P)], n)          # down into the box, so it is not also a new high
        for t in range(s, last):
            closes[t] = P + 0.03 * P * math.sin(2 * math.pi * (t - s) / 10)
        rows = _rows_from_closes(_noisy(closes, rng, 0.0015), end_ts, secs, rng, 0.001, vbase)
        top = max(r[2] for r in rows[s:last])
        _replace(rows, last, rows[prev][4], top * 1.025, rng, 0.002, vbase * breakout_volume)
    else:
        raise ValueError(kind)
    return rows


def _extend(rows, ext, secs, rng, sigma_daily, vbase) -> list[Row]:
    """Random candles after the last one (they only become visible when the clock reaches them)."""
    sig = sigma_daily * math.sqrt(secs / 86_400)
    p = rows[-1][4]
    ts = rows[-1][0]
    out = list(rows)
    for _ in range(ext):
        o = p
        p = p * math.exp(rng.gauss(0, sig))
        ts += secs
        _o, h, l, c, v = _bar(rng, o, p, sig, _wobble(rng, vbase))
        out.append([ts, o, h, l, c, v])
    return out


# ------------------------------------------------------------------------------ the exchange
@dataclass
class DemoCoin:
    base: str
    kind: str                    # random | stable | ath | high365 | low365 | w | range
    price: float
    sigma: float
    volumes: dict = field(default_factory=dict)       # venue -> 24h USDT volume
    rank: int = 0


class DemoExchange:
    VENUE_CYCLE = ("binance", "bybit", "okx", "kucoin", "gateio")

    def __init__(self, n_coins: int = 640, days: int = 800, clock=time.time, seed: int = 11,
                 ext_candles: int = 40, venues=None) -> None:
        self.clock = clock
        self.days = days
        self.ext = ext_candles
        self.venues = tuple(venues or self.VENUE_CYCLE)
        self.n_coins = n_coins
        self.fail_venues: set[str] = set()
        self.dropped: set[tuple[str, str]] = set()              # (venue, base) no longer listed
        self.calls = {"markets": 0, "ohlcv": 0}
        self._anchor = {tf: int(self.clock() // s) * s - s for tf, s in TF_SECONDS.items()}
        self._cache: dict[tuple[str, str, str], list[Row]] = {}
        self.coins: dict[str, DemoCoin] = {}
        self.sectors: dict[str, str] = {}
        self._make_coins(seed)

    # ---- the fake coin list
    def _make_coins(self, seed: int) -> None:
        rng = random.Random(seed)
        named = [(b, "random") for b in REAL_TICKERS if b not in ("JUP", "SYRUP")]
        # important coins first, then pattern coins (kept in the top ranks), then the rest
        order: list[tuple[str, str]] = []
        order += named[:14]
        order += [(b, k) for b, k in INJECTED.items()]
        order += [("JUP", "random"), ("SYRUP", "random")]
        order += list(JUNK.items())
        order += named[14:]
        used = {b for b, _ in order}
        cons, vow = "BCDFGKLMNPRSTVZ", "AEIOU"
        i = 0
        while len(order) < self.n_coins:
            i += 1
            r = random.Random(i * 7919)
            name = r.choice(cons) + r.choice(vow) + r.choice(cons) + r.choice(vow) + (r.choice(cons) if i % 3 else "")
            if name not in used and not name.endswith(("UP", "DOWN")):
                used.add(name)
                order.append((name, "random"))
        order = order[: self.n_coins]
        for rank, (base, kind) in enumerate(order):
            vol = 3e9 * math.exp(-0.01357 * rank)
            price = 1.0 if kind == "stable" else math.exp(rng.uniform(math.log(0.02), math.log(1500)))
            c = DemoCoin(base, kind, price, rng.uniform(0.025, 0.06), rank=rank)
            primary = self.VENUE_CYCLE[rank % len(self.VENUE_CYCLE)]
            c.volumes[primary] = vol
            if rank < 150 or rank % 4 == 0:
                other = self.VENUE_CYCLE[(rank + 1 + rank % 3) % len(self.VENUE_CYCLE)]
                c.volumes.setdefault(other, vol * (0.3 + 0.1 * (rank % 4)))
            self.coins[base] = c
            if base in DEMO_SECTOR_OF:
                self.sectors[base] = DEMO_SECTOR_OF[base]
            elif kind in ("random", "ath", "high365", "low365", "w", "range"):
                self.sectors[base] = SECTORS[(rank * 5 + 3) % (len(SECTORS) - 1)]

    # ---- interface
    async def list_markets(self, venue: str) -> list[MarketInfo]:
        self.calls["markets"] += 1
        if venue in self.fail_venues:
            raise RuntimeError(f"{venue}: simulated outage")
        return [MarketInfo(venue, c.base, f"{c.base}/USDT", c.volumes[venue], c.price)
                for c in self.coins.values() if venue in c.volumes and (venue, c.base) not in self.dropped]

    def _series(self, coin: DemoCoin, venue: str, tf: str) -> list[Row]:
        key = (coin.base, venue, tf)
        rows = self._cache.get(key)
        if rows is None:
            secs = TF_SECONDS[tf]
            end = self._anchor[tf]
            n = {"1d": self.days, "4h": 1500, "1h": 1000}[tf]
            rng = random.Random(f"{coin.base}-{venue}-{tf}")
            kind = coin.kind
            if kind in ("ath", "high365", "low365") and tf != "1d":
                kind = "random"
            if kind in ("ath", "high365", "low365"):
                n = max(n, 520)
            if kind == "stable":
                rows = stable_series(n, end, secs, rng)
            elif kind == "random":
                rows = random_walk(n, end, secs, rng, coin.price, coin.sigma)
            else:
                rows = pattern_series(kind, n, end, secs, rng, price=coin.price)
            rows = _extend(rows, self.ext, secs, rng, coin.sigma, 1000.0)
            self._cache[key] = rows
        return rows

    async def ohlcv(self, venue: str, symbol: str, tf: str, since_ts: int | None, limit: int = 1000) -> list[Row]:
        self.calls["ohlcv"] += 1
        if venue in self.fail_venues:
            raise RuntimeError(f"{venue}: simulated outage")
        base = symbol.split("/")[0]
        coin = self.coins.get(base)
        if coin is None or venue not in coin.volumes or (venue, base) in self.dropped:
            raise KeyError(f"{venue} has no {symbol}")
        secs = TF_SECONDS[tf]
        now = self.clock()
        current_open = int(now // secs) * secs               # candle still forming (included, like real exchanges)
        rows = [r for r in self._series(coin, venue, tf) if r[0] <= current_open]
        if since_ts is not None:
            rows = [r for r in rows if r[0] >= since_ts]
            return [list(r) for r in rows[:limit]]
        return [list(r) for r in rows[-limit:]]

    async def fetch_ticker(self, venue: str, symbol: str) -> dict:
        base = symbol.split("/")[0]
        coin = self.coins.get(base)
        last = coin.price if coin else 1.0
        return {
            "symbol": symbol,
            "venue": venue,
            "last": last,
            "bid": last * 0.999,
            "ask": last * 1.001,
            "high_24h": last * 1.05,
            "low_24h": last * 0.95,
            "change_24h_pct": 1.25,
            "quote_volume": 10_000_000.0,
            "timestamp": int(self.clock() * 1000),
        }

    async def close(self) -> None:
        pass
