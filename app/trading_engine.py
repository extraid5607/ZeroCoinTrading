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
    mode: str = "paper"  # "paper", "live", or "both"
    auto_trade_paper: bool = False
    auto_trade_live: bool = False
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
        mode = str(d.get("mode", "paper"))
        master_auto = bool(d.get("auto_trade", False))
        auto_paper = bool(d.get("auto_trade_paper", master_auto if mode in ("paper", "both") else False))
        auto_live = bool(d.get("auto_trade_live", master_auto if mode in ("live", "both") else False))
        return cls(
            auto_trade=master_auto,
            mode=mode,
            auto_trade_paper=auto_paper,
            auto_trade_live=auto_live,
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

        # Separate positions by mode
        paper_opens = [p for p in opens if p.mode == "paper"]
        live_opens = [p for p in opens if p.mode == "live"]
        paper_closed = [p for p in closed if p.mode == "paper"]
        live_closed = [p for p in closed if p.mode == "live"]

        # Paper stats
        paper_unrealized = sum(p.pnl for p in paper_opens)
        paper_realized = sum(p.pnl for p in paper_closed)
        paper_wins = sum(1 for p in paper_closed if p.pnl > 0)
        paper_win_rate = (paper_wins / len(paper_closed) * 100) if paper_closed else 0.0

        # Live stats
        live_balance = 0.0
        live_equity = 0.0
        live_error = ""
        if self.delta.is_authenticated():
            try:
                live_bal = await self.delta.fetch_balance()
                live_balance = float(live_bal.get("free", 0.0))
                live_equity = float(live_bal.get("total", live_balance))
            except Exception as exc:
                live_error = f"{type(exc).__name__}: {str(exc)[:120]}"
                log.warning("Failed to fetch live balance for overview: %s", exc)

        live_unrealized = sum(p.pnl for p in live_opens)
        live_realized = sum(p.pnl for p in live_closed)
        live_wins = sum(1 for p in live_closed if p.pnl > 0)
        live_win_rate = (live_wins / len(live_closed) * 100) if live_closed else 0.0

        return {
            "auto_trade": self.settings.auto_trade,
            "mode": self.settings.mode,
            "region": self.settings.region,
            "delta_connected": self.delta.is_authenticated(),
            "live_error": live_error,
            "max_positions": self.settings.max_positions,
            "risk_pct": self.settings.risk_per_trade_pct,
            "leverage": self.settings.leverage,
            "paper": {
                "balance": round(self.settings.virtual_balance, 2),
                "equity": round(self.settings.virtual_balance + paper_unrealized, 2),
                "unrealized_pnl": round(paper_unrealized, 2),
                "realized_pnl": round(paper_realized, 2),
                "win_rate": round(paper_win_rate, 1),
                "total_trades": len(paper_closed),
                "open_count": len(paper_opens),
            },
            "live": {
                "balance": round(live_balance, 2),
                "equity": round(live_equity, 2),
                "unrealized_pnl": round(live_unrealized, 2),
                "realized_pnl": round(live_realized, 2),
                "win_rate": round(live_win_rate, 1),
                "total_trades": len(live_closed),
                "open_count": len(live_opens),
            },
            # Top-level fallback for backward compatibility
            "balance": round(live_balance if self.settings.mode == "live" else self.settings.virtual_balance, 2),
            "equity": round(live_equity if self.settings.mode == "live" else (self.settings.virtual_balance + paper_unrealized), 2),
            "unrealized_pnl": round(live_unrealized if self.settings.mode == "live" else paper_unrealized, 2),
            "realized_pnl": round(live_realized if self.settings.mode == "live" else paper_realized, 2),
            "win_rate": round(live_win_rate if self.settings.mode == "live" else paper_win_rate, 1),
            "total_trades": len(live_closed if self.settings.mode == "live" else paper_closed),
            "open_count": len(live_opens if self.settings.mode == "live" else paper_opens),
        }

    # ---- Signal Execution -----------------------------------------------------
    async def process_signal(self, coin_base: str, symbol: str, pattern: str, entry: float, stop: float, target: float | None = None) -> bool:
        if not self.settings.auto_trade:
            return False

        if pattern not in self.settings.enabled_patterns:
            return False

        modes_to_execute = []
        if self.settings.mode == "both":
            modes_to_execute = ["paper", "live"]
        elif self.settings.mode == "live":
            modes_to_execute = ["live"]
        else:
            modes_to_execute = ["paper"]

        executed_any = False
        tp = target if (target and target > entry) else entry + 2.0 * (entry - stop)
        now = time.time()

        async with self._lock:
            for exec_mode in modes_to_execute:
                with self.db.session() as s:
                    open_bases = set(s.scalars(select(TradePosition.base).where(TradePosition.status == "open", TradePosition.mode == exec_mode)))

                if len(open_bases) >= self.settings.max_positions:
                    continue

                if coin_base.upper() in open_bases:
                    continue

                effective_balance = self.settings.virtual_balance
                if exec_mode == "live":
                    if not self.delta.is_authenticated():
                        continue
                    try:
                        delta_bal = await self.delta.fetch_balance()
                        if delta_bal.get("free", 0.0) > 0:
                            effective_balance = float(delta_bal["free"])
                    except Exception as e:
                        log.warning("Could not fetch real Delta balance for sizing: %s", e)

                risk_usd = effective_balance * (self.settings.risk_per_trade_pct / 100.0)
                risk_per_unit = max(entry * 0.005, abs(entry - stop))
                size = risk_usd / risk_per_unit

                max_notional = (effective_balance * self.settings.leverage) / self.settings.max_positions
                if size * entry > max_notional:
                    size = max_notional / entry

                size = round(size, 4)
                if size <= 0:
                    continue

                if exec_mode == "live" and self.delta.is_authenticated():
                    try:
                        await self.delta.create_bracket_order(symbol, "buy", size, stop_loss=stop, take_profit=tp)
                        log.info("Live bracket order executed on Delta for %s", symbol)
                    except Exception as exc:
                        log.error("Failed to execute live order on Delta: %s", exc)
                        continue

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
                    mode=exec_mode,
                    created_at=now,
                    updated_at=now,
                )
                with self.db.session() as s:
                    s.add(pos)
                    s.commit()
                log.info("Opened %s trade on %s at %s, SL: %s, TP: %s", exec_mode, coin_base, entry, stop, tp)
                executed_any = True

        return executed_any

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
