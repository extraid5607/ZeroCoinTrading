"""Trading Engine: handles automated Paper Trading and live Delta Exchange execution."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass

from sqlalchemy import delete, select

from .db import Database, TradePosition, kv_get, kv_set
from .delta_client import DeltaClient

log = logging.getLogger("trading_engine")

DEFAULT_VIRTUAL_BALANCE = 10_000.0


@dataclass
class TradingSettings:
    auto_trade: bool = False
    mode: str = "paper"  # "paper" or "live"
    virtual_balance: float = DEFAULT_VIRTUAL_BALANCE
    risk_per_trade_pct: float = 2.0  # risk 2% of portfolio per trade
    max_positions: int = 4
    leverage: int = 3
    region: str = "india"  # "india" or "global"
    enabled_patterns: tuple[str, ...] = ("ath", "high365", "w", "range")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["enabled_patterns"] = list(self.enabled_patterns)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TradingSettings":
        pats = tuple(d.get("enabled_patterns", ["ath", "high365", "w", "range"]))
        return cls(
            auto_trade=bool(d.get("auto_trade", False)),
            mode=str(d.get("mode", "paper")),
            virtual_balance=float(d.get("virtual_balance", DEFAULT_VIRTUAL_BALANCE)),
            risk_per_trade_pct=float(d.get("risk_per_trade_pct", 2.0)),
            max_positions=int(d.get("max_positions", 4)),
            leverage=int(d.get("leverage", 3)),
            region=str(d.get("region", "india")),
            enabled_patterns=pats,
        )


class TradingEngine:
    def __init__(self, db: Database, delta_client: DeltaClient | None = None) -> None:
        self.db = db
        self.delta = delta_client or DeltaClient()
        self._lock = asyncio.Lock()
        self.settings = self._load_settings()

    def _load_settings(self) -> TradingSettings:
        with self.db.session() as s:
            raw = kv_get(s, "trading_settings", {})
        if raw and isinstance(raw, dict):
            return TradingSettings.from_dict(raw)
        return TradingSettings()

    def save_settings(self, new_settings: TradingSettings) -> None:
        self.settings = new_settings
        with self.db.session() as s:
            kv_set(s, "trading_settings", new_settings.to_dict())

    def update_config(self, **kwargs) -> TradingSettings:
        d = self.settings.to_dict()
        for k, v in kwargs.items():
            if k in d:
                d[k] = v
        updated = TradingSettings.from_dict(d)
        self.save_settings(updated)
        return updated

    def toggle_auto_trade(self, enabled: bool | None = None) -> bool:
        new_state = (not self.settings.auto_trade) if enabled is None else enabled
        self.update_config(auto_trade=new_state)
        return new_state

    # ---- Positions & Account Status -------------------------------------------
    def get_open_positions(self) -> list[TradePosition]:
        with self.db.session() as s:
            return list(s.scalars(select(TradePosition).where(TradePosition.status == "open").order_by(TradePosition.id.desc())))

    def get_closed_positions(self, limit: int = 30) -> list[TradePosition]:
        with self.db.session() as s:
            return list(s.scalars(select(TradePosition).where(TradePosition.status != "open").order_by(TradePosition.id.desc()).limit(limit)))

    async def calculate_overview(self) -> dict:
        opens = self.get_open_positions()
        closed = self.get_closed_positions(50)

        unrealized_pnl = sum(p.pnl for p in opens)
        realized_pnl = sum(p.pnl for p in closed)
        wins = sum(1 for p in closed if p.pnl > 0)
        total_closed = len(closed)
        win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0

        balance = self.settings.virtual_balance
        equity = balance + unrealized_pnl

        # When in LIVE mode and Delta is authenticated, show real live account margin
        if self.settings.mode == "live" and self.delta.is_authenticated():
            try:
                live_bal = await self.delta.fetch_balance()
                balance = float(live_bal.get("free", 0.0))
                total_val = float(live_bal.get("total", balance))
                equity = total_val
            except Exception as exc:
                log.warning("Failed to fetch live balance for overview: %s", exc)

        return {
            "auto_trade": self.settings.auto_trade,
            "mode": self.settings.mode,
            "region": self.settings.region,
            "balance": round(balance, 2),
            "equity": round(equity, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "realized_pnl": round(realized_pnl, 2),
            "win_rate": round(win_rate, 1),
            "total_trades": total_closed,
            "open_count": len(opens),
            "max_positions": self.settings.max_positions,
            "risk_pct": self.settings.risk_per_trade_pct,
            "leverage": self.settings.leverage,
            "delta_connected": self.delta.is_authenticated(),
        }

    # ---- Signal Execution -----------------------------------------------------
    async def process_signal(self, coin_base: str, symbol: str, pattern: str, entry: float, stop: float, target: float | None = None) -> bool:
        if not self.settings.auto_trade:
            return False

        if pattern not in self.settings.enabled_patterns:
            return False

        async with self._lock:
            with self.db.session() as s:
                active_count = s.scalar(select(TradePosition).where(TradePosition.status == "open").with_only_columns(TradePosition.id))
                open_bases = set(s.scalars(select(TradePosition.base).where(TradePosition.status == "open")))

            if len(open_bases) >= self.settings.max_positions:
                return False

            if coin_base.upper() in open_bases:
                return False  # Already have an open trade on this coin

            # 1. Position Sizing
            effective_balance = self.settings.virtual_balance
            if self.settings.mode == "live" and self.delta.is_authenticated():
                try:
                    delta_bal = await self.delta.fetch_balance()
                    if delta_bal.get("free", 0.0) > 0:
                        effective_balance = float(delta_bal["free"])
                except Exception as e:
                    log.warning("Could not fetch real Delta balance for sizing: %s", e)

            risk_usd = effective_balance * (self.settings.risk_per_trade_pct / 100.0)
            risk_per_unit = max(entry * 0.005, abs(entry - stop))
            size = risk_usd / risk_per_unit

            # Max notional cap: balance * leverage / max_positions
            max_notional = (effective_balance * self.settings.leverage) / self.settings.max_positions
            if size * entry > max_notional:
                size = max_notional / entry

            size = round(size, 4)
            if size <= 0:
                return False

            tp = target if (target and target > entry) else entry + 2.0 * (entry - stop)
            now = time.time()

            # 2. Execution (Paper or Live)
            if self.settings.mode == "live" and self.delta.is_authenticated():
                try:
                    await self.delta.create_bracket_order(symbol, "buy", size, stop_loss=stop, take_profit=tp)
                    log.info("Live bracket order executed on Delta for %s", symbol)
                except Exception as exc:
                    log.error("Failed to execute live order on Delta: %s", exc)
                    return False

            # Record position
            pos = TradePosition(
                symbol=symbol,
                base=coin_base.upper(),
                venue="delta",
                side="long",
                size=size,
                entry_price=entry,
                current_price=entry,
                stop_loss=stop,
                take_profit=tp,
                pattern=pattern,
                pnl=0.0,
                pnl_pct=0.0,
                status="open",
                mode=self.settings.mode,
                created_at=now,
                updated_at=now,
            )
            with self.db.session() as s:
                s.add(pos)
                s.commit()
            log.info("Opened %s trade on %s at %s, SL: %s, TP: %s", self.settings.mode, coin_base, entry, stop, tp)
            return True

    # ---- Live Position Tracking & Trailing Stops -----------------------------
    def update_open_positions(self, live_prices: dict[str, float]) -> list[dict]:
        closed_events = []
        with self.db.session() as s:
            positions = list(s.scalars(select(TradePosition).where(TradePosition.status == "open")))
            now = time.time()

            for p in positions:
                cur = live_prices.get(p.base) or live_prices.get(p.symbol)
                if not cur or cur <= 0:
                    continue

                p.current_price = cur
                p.updated_at = now
                pnl = (cur - p.entry_price) * p.size
                pnl_pct = (cur - p.entry_price) / p.entry_price * 100.0
                p.pnl = round(pnl, 2)
                p.pnl_pct = round(pnl_pct, 2)

                # Check Stop Loss
                if cur <= p.stop_loss:
                    p.status = "stopped"
                    p.closed_at = now
                    p.close_price = cur
                    p.exit_reason = "Stop Loss Hit"
                    self.settings.virtual_balance += pnl
                    closed_events.append({"id": p.id, "base": p.base, "reason": "Stop Loss", "pnl": pnl})

                # Check Take Profit
                elif cur >= p.take_profit:
                    p.status = "target_hit"
                    p.closed_at = now
                    p.close_price = cur
                    p.exit_reason = "Target Profit Reached"
                    self.settings.virtual_balance += pnl
                    closed_events.append({"id": p.id, "base": p.base, "reason": "Target Hit", "pnl": pnl})

            if closed_events:
                kv_set(s, "trading_settings", self.settings.to_dict())
            s.commit()

        return closed_events

    # ---- Manual Position Actions ----------------------------------------------
    def close_position(self, pos_id: int) -> bool:
        with self.db.session() as s:
            pos = s.get(TradePosition, pos_id)
            if not pos or pos.status != "open":
                return False
            now = time.time()
            pos.status = "closed"
            pos.closed_at = now
            pos.close_price = pos.current_price
            pos.exit_reason = "Manual Close"
            self.settings.virtual_balance += pos.pnl
            kv_set(s, "trading_settings", self.settings.to_dict())
            s.commit()
            return True

    def close_all_positions(self) -> int:
        with self.db.session() as s:
            positions = list(s.scalars(select(TradePosition).where(TradePosition.status == "open")))
            now = time.time()
            count = 0
            for pos in positions:
                pos.status = "closed"
                pos.closed_at = now
                pos.close_price = pos.current_price
                pos.exit_reason = "Panic Close All"
                self.settings.virtual_balance += pos.pnl
                count += 1
            if count:
                kv_set(s, "trading_settings", self.settings.to_dict())
            s.commit()
            return count

    def reset_paper_wallet(self) -> float:
        with self.db.session() as s:
            s.execute(delete(TradePosition))
            self.settings.virtual_balance = DEFAULT_VIRTUAL_BALANCE
            kv_set(s, "trading_settings", self.settings.to_dict())
            s.commit()
        return DEFAULT_VIRTUAL_BALANCE
