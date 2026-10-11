"""Delta Exchange API client supporting both public market feeds and authenticated trading.

Supports Delta Global (api.delta.exchange) and Delta India (api.india.delta.exchange).
Forces IPv4 socket connection to avoid IPv6 happy eyeballs timeouts.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
from typing import Any

import aiohttp
import ccxt.async_support as ccxt
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("delta_client")

DELTA_URLS = {
    "global": {
        "api": {
            "public": "https://api.delta.exchange",
            "private": "https://api.delta.exchange",
        },
        "test": {
            "public": "https://testnet-api.delta.exchange",
            "private": "https://testnet-api.delta.exchange",
        },
    },
    "india": {
        "api": {
            "public": "https://api.india.delta.exchange",
            "private": "https://api.india.delta.exchange",
        },
        "test": {
            "public": "https://testnet-api.delta.exchange",
            "private": "https://testnet-api.delta.exchange",
        },
    },
}


class DeltaClient:
    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        region: str = "india",
        testnet: bool = False,
    ) -> None:
        self.api_key = api_key or os.environ.get("DELTA_API_KEY", "")
        self.api_secret = api_secret or os.environ.get("DELTA_API_SECRET", "")
        self.region = region.lower() if region.lower() in ("global", "india") else "india"
        self.testnet = testnet
        self._exchange: ccxt.delta | None = None
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    async def _get_exchange(self) -> ccxt.delta:
        if self._exchange is not None:
            return self._exchange

        async with self._lock:
            if self._exchange is not None:
                return self._exchange

            # Force IPv4 socket resolution to prevent aiohttp timeouts on dual-stack networks
            connector = aiohttp.TCPConnector(family=socket.AF_INET)
            self._session = aiohttp.ClientSession(connector=connector)

            urls = DELTA_URLS.get(self.region, DELTA_URLS["india"])
            config: dict[str, Any] = {
                "session": self._session,
                "enableRateLimit": True,
                "timeout": 15000,
                "urls": urls,
                "options": {
                    "defaultType": "swap",  # trade swap/perpetual futures contracts
                },
            }
            if self.api_key and self.api_secret:
                config["apiKey"] = self.api_key
                config["secret"] = self.api_secret

            ex = ccxt.delta(config)
            if self.testnet:
                ex.set_sandbox_mode(True)

            self._exchange = ex
            return ex

    async def close(self) -> None:
        if self._exchange is not None:
            try:
                await self._exchange.close()
            except Exception:
                pass
            self._exchange = None
        if self._session is not None and not self._session.closed:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None

    # ---- Public market data ----------------------------------------------------
    async def fetch_markets(self) -> dict:
        ex = await self._get_exchange()
        if not ex.markets:
            await ex.load_markets()
        return ex.markets

    async def fetch_ticker(self, symbol: str) -> dict:
        ex = await self._get_exchange()
        t = await ex.fetch_ticker(symbol)
        return {
            "symbol": symbol,
            "last": t.get("last") or t.get("close") or 0.0,
            "bid": t.get("bid"),
            "ask": t.get("ask"),
            "high": t.get("high"),
            "low": t.get("low"),
            "change_pct": t.get("percentage"),
            "quote_volume": t.get("quoteVolume"),
            "timestamp": t.get("timestamp"),
        }

    async def fetch_tickers(self, symbols: list[str] | None = None) -> dict[str, dict]:
        ex = await self._get_exchange()
        tickers = await ex.fetch_tickers(symbols)
        out = {}
        for sym, t in tickers.items():
            out[sym] = {
                "symbol": sym,
                "last": t.get("last") or t.get("close") or 0.0,
                "bid": t.get("bid"),
                "ask": t.get("ask"),
                "change_pct": t.get("percentage"),
            }
        return out

    async def fetch_ohlcv(self, symbol: str, tf: str = "1d", since: int | None = None, limit: int = 100) -> list:
        ex = await self._get_exchange()
        since_ms = int(since * 1000) if since else None
        rows = await ex.fetch_ohlcv(symbol, tf, since_ms, limit)
        return [[r[0] / 1000, r[1], r[2], r[3], r[4], r[5] or 0.0] for r in rows if None not in r[:5]]

    # ---- Authenticated trading methods ----------------------------------------
    def is_authenticated(self) -> bool:
        return bool(self.api_key and self.api_secret)

    async def fetch_balance(self) -> dict:
        if not self.is_authenticated():
            raise PermissionError("Delta API key and secret are not configured.")
        ex = await self._get_exchange()
        bal = await ex.fetch_balance()
        # Delta India accounts are typically denominated in USD or USDT
        usd_info = bal.get("USD") or bal.get("USDT") or {}
        free = float(usd_info.get("free", 0.0) if isinstance(usd_info, dict) else (bal.get("free", {}).get("USD", 0.0) or 0.0))
        used = float(usd_info.get("used", 0.0) if isinstance(usd_info, dict) else (bal.get("used", {}).get("USD", 0.0) or 0.0))
        total = float(usd_info.get("total", 0.0) if isinstance(usd_info, dict) else (bal.get("total", {}).get("USD", 0.0) or 0.0))
        return {
            "free": round(free, 2),
            "used": round(used, 2),
            "total": round(total, 2),
            "raw": bal.get("total", {}),
        }

    async def fetch_positions(self) -> list[dict]:
        if not self.is_authenticated():
            return []
        ex = await self._get_exchange()
        try:
            positions = await ex.fetch_positions()
            out = []
            for p in positions:
                contracts = float(p.get("contracts") or 0.0)
                if abs(contracts) > 0:
                    out.append({
                        "symbol": p.get("symbol"),
                        "side": "long" if contracts > 0 else "short",
                        "size": abs(contracts),
                        "entry_price": float(p.get("entryPrice") or 0.0),
                        "mark_price": float(p.get("markPrice") or 0.0),
                        "unrealized_pnl": float(p.get("unrealizedPnl") or 0.0),
                        "leverage": float(p.get("leverage") or 1.0),
                    })
            return out
        except Exception as exc:
            log.warning("fetch_positions failed: %s", exc)
            return []

    async def create_market_order(self, symbol: str, side: str, amount: float) -> dict:
        if not self.is_authenticated():
            raise PermissionError("Delta API credentials required for live order placement.")
        ex = await self._get_exchange()
        order = await ex.create_order(symbol, "market", side.lower(), amount)
        return {
            "order_id": order.get("id"),
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "price": order.get("price") or order.get("average"),
            "status": order.get("status"),
        }

    async def create_bracket_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_loss: float,
        take_profit: float | None = None,
    ) -> dict:
        """Place entry market order followed by stop-loss trigger."""
        if not self.is_authenticated():
            raise PermissionError("Delta API credentials required for live trading.")
        ex = await self._get_exchange()

        # 1. Place primary market entry
        entry = await ex.create_order(symbol, "market", side.lower(), amount)

        # 2. Place stop-loss trigger order
        close_side = "sell" if side.lower() == "buy" else "buy"
        sl_params = {
            "stopPrice": stop_loss,
            "reduceOnly": True,
        }
        sl_order = None
        try:
            sl_order = await ex.create_order(symbol, "stop", close_side, amount, params=sl_params)
        except Exception as exc:
            log.error("Failed to place stop loss on Delta: %s", exc)

        tp_order = None
        if take_profit:
            try:
                tp_params = {"reduceOnly": True}
                tp_order = await ex.create_order(symbol, "limit", close_side, amount, take_profit, params=tp_params)
            except Exception as exc:
                log.warning("Failed to place take profit on Delta: %s", exc)

        return {
            "entry_order": entry,
            "sl_order": sl_order,
            "tp_order": tp_order,
        }

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        if not self.is_authenticated():
            return False
        ex = await self._get_exchange()
        try:
            await ex.cancel_order(order_id, symbol)
            return True
        except Exception as exc:
            log.warning("cancel_order error: %s", exc)
            return False
