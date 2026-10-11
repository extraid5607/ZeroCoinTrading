"""Exchange access. One small interface, with a CCXT version for real data and a fake one for demo
and tests (see demo.py). All calls are public market-data calls: no API keys."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger("exchange")

# Candle row: [open time (epoch seconds), open, high, low, close, volume]
Row = list


@dataclass
class MarketInfo:
    venue: str
    base: str
    symbol: str
    quote_volume: float      # 24h volume in USDT (about USD)
    last_price: float


class Exchange(Protocol):
    async def list_markets(self, venue: str) -> list[MarketInfo]: ...
    async def ohlcv(self, venue: str, symbol: str, tf: str, since_ts: int | None, limit: int = 1000) -> list[Row]: ...
    async def fetch_ticker(self, venue: str, symbol: str) -> dict: ...
    async def close(self) -> None: ...


# Our venue names -> CCXT exchange ids (CCXT calls Gate.io "gate", not "gateio").
CCXT_IDS = {"gateio": "gate"}


def ccxt_class(venue: str):
    import ccxt.async_support as ccxt_async
    cls = getattr(ccxt_async, CCXT_IDS.get(venue, venue), None)
    if cls is None:
        raise ValueError(f"CCXT has no exchange called {venue!r}")
    return cls


class CcxtExchange:
    """Real exchanges through CCXT (public endpoints only)."""
    BACKOFF = (2.0, 5.0, 12.0, 30.0)

    def __init__(self, concurrency: int = 4, client_factory=None, sleep=asyncio.sleep) -> None:
        self._concurrency = concurrency
        self._factory = client_factory
        self._sleep = sleep
        self._clients: dict[str, object] = {}
        self._sems: dict[str, asyncio.Semaphore] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _sem(self, venue: str) -> asyncio.Semaphore:
        return self._sems.setdefault(venue, asyncio.Semaphore(self._concurrency))

    async def _client(self, venue: str):
        ex = self._clients.get(venue)
        if ex is None:
            if self._factory:
                ex = self._factory(venue)
            else:
                ex = ccxt_class(venue)({
                    "enableRateLimit": True, "timeout": 15_000,
                    "options": {"fetchCurrencies": False, "defaultType": "spot"},
                })
            self._clients[venue] = ex
        if not ex.markets:
            lock = self._locks.setdefault(venue, asyncio.Lock())
            async with lock:
                if not ex.markets:
                    await ex.load_markets()
        return ex

    async def _retry(self, venue: str, label: str, call):
        """Run a call; on rate-limit style errors wait longer each time instead of hammering."""
        import ccxt
        last: Exception | None = None
        for wait in (0.0, *self.BACKOFF):
            if wait:
                log.warning("%s %s: rate limited, waiting %.0fs", venue, label, wait)
                await self._sleep(wait)
            try:
                async with self._sem(venue):
                    return await call()
            except (ccxt.RateLimitExceeded, ccxt.DDoSProtection) as exc:
                last = exc
        raise RuntimeError(f"{venue} {label}: still rate limited after retries: {last}")

    async def list_markets(self, venue: str) -> list[MarketInfo]:
        ex = await self._client(venue)
        tickers = await self._retry(venue, "tickers", ex.fetch_tickers)
        out: list[MarketInfo] = []
        for sym, m in ex.markets.items():
            if not m.get("spot") or m.get("quote") != "USDT" or m.get("active") is False:
                continue
            t = tickers.get(sym) or {}
            last = t.get("last") or t.get("close") or 0.0
            qv = t.get("quoteVolume")
            if qv is None and t.get("baseVolume") and last:
                qv = t["baseVolume"] * last
            if not last or not qv:
                continue
            out.append(MarketInfo(venue, str(m["base"]).upper(), sym, float(qv), float(last)))
        return out

    async def ohlcv(self, venue: str, symbol: str, tf: str, since_ts: int | None, limit: int = 1000) -> list[Row]:
        ex = await self._client(venue)
        since_ms = int(since_ts * 1000) if since_ts is not None else None
        rows = await self._retry(venue, f"ohlcv {symbol}", lambda: ex.fetch_ohlcv(symbol, tf, since_ms, limit))
        return [[r[0] / 1000, r[1], r[2], r[3], r[4], r[5] or 0.0] for r in rows if None not in r[:5]]

    async def fetch_ticker(self, venue: str, symbol: str) -> dict:
        ex = await self._client(venue)
        t = await self._retry(venue, f"ticker {symbol}", lambda: ex.fetch_ticker(symbol))
        return {
            "symbol": symbol,
            "venue": venue,
            "last": t.get("last") or t.get("close") or 0.0,
            "bid": t.get("bid"),
            "ask": t.get("ask"),
            "high_24h": t.get("high"),
            "low_24h": t.get("low"),
            "change_24h_pct": t.get("percentage"),
            "quote_volume": t.get("quoteVolume"),
            "timestamp": t.get("timestamp"),
        }

    async def close(self) -> None:
        for ex in self._clients.values():
            try:
                await ex.close()
            except Exception:  # pragma: no cover
                pass
