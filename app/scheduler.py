"""The pipeline (universe -> candles -> scan) and the scheduler that runs it on time.

State, not clock times, decides what is due: for each timeframe the last finished cycle is stored,
and a cycle is due when a newer completed candle exists. That is why a restart or a missed scan
simply catches up by itself. All work runs in the background; a user request never touches an exchange."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import func, select

from .candles import (count_coins_with_candles, fill_gaps, find_gaps, latest_closed, update_all)
from .config import TF_SECONDS, Settings
from .db import Candle, Coin, Database, ScanRun, kv_get, kv_set
from .exchange import Exchange
from .scanner import scan_timeframe
from .universe import apply_stable_rule, load_sectors, refresh_universe

log = logging.getLogger("scheduler")


def _day(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")


class Pipeline:
    def __init__(self, db: Database, api: Exchange, settings: Settings, clock=time.time, sleep=asyncio.sleep,
                 sectors: dict | None = None, trading_engine=None) -> None:
        self.db, self.api, self.settings = db, api, settings
        self.clock, self.sleep = clock, sleep
        self.sectors = sectors
        self.trading_engine = trading_engine
        self.outlier_seen: dict = {}
        self.venue_errors: dict[str, str] = {}
        self.phase = "starting"
        self._lock = asyncio.Lock()

    # ---- steps ---------------------------------------------------------------------------
    def _active(self) -> list[Coin]:
        with self.db.session() as s:
            return list(s.scalars(select(Coin).where(Coin.active.is_(True)).order_by(Coin.quote_volume.desc())))

    async def refresh_universe(self):
        self.phase = "refreshing the coin list"
        sectors = self.sectors if self.sectors is not None else load_sectors()
        rep = await refresh_universe(self.db, self.api, self.settings, sectors, self.clock())
        with self.db.session() as s:
            kv_set(s, "universe_day", _day(self.clock()))
        return rep

    async def update_candles(self, tf: str) -> dict:
        self.phase = f"downloading {tf} candles"
        coins = self._active()
        out = await update_all(self.db, self.api, coins, tf, self.settings, self.clock(), self.outlier_seen)
        venue_of = {c.base: c.venue for c in coins}
        for base, msg in out["errors"].items():
            self.venue_errors[venue_of.get(base, "?")] = f"{base}: {msg}"
        return out

    def missing_and_stale(self, tf: str) -> tuple[list[str], int]:
        """Coins that updated yesterday but not today (worth waiting for), and coins that stopped long ago."""
        secs = TF_SECONDS[tf]
        want = latest_closed(tf, self.clock())
        with self.db.session() as s:
            rows = s.execute(select(Coin.base, func.max(Candle.ts)).join(Candle, Candle.coin_id == Coin.id)
                             .where(Coin.active.is_(True), Candle.tf == tf).group_by(Coin.base)).all()
        missing = [b for b, ts in rows if ts == want - secs]
        stale = sum(1 for _, ts in rows if ts < want - secs)
        return missing, stale

    async def run_cycle(self, tf: str, force: bool = False) -> dict:
        """Download new candles (waiting a while for late ones), then scan."""
        secs = TF_SECONDS[tf]
        want = latest_closed(tf, self.clock())
        close_time = want + secs
        budget = self.settings.retry_max_s - (self.clock() - close_time)       # only wait if the candle just closed
        out = await self.update_candles(tf)
        missing, stale = self.missing_and_stale(tf)
        while missing and budget > 0:
            self.phase = f"waiting for {len(missing)} late {tf} candles"
            await self.sleep(self.settings.retry_interval_s)
            budget -= max(self.settings.retry_interval_s, 1)
            out = await self.update_candles(tf)
            missing, stale = self.missing_and_stale(tf)
        if tf == "1d":
            await asyncio.to_thread(apply_stable_rule, self.db)
        self.phase = f"scanning {tf}"
        result = await asyncio.to_thread(scan_timeframe, self.db, tf, self.settings, self.clock(), len(missing))
        due_time = close_time + self.settings.scan_delay_s.get(tf, 300)
        with self.db.session() as s:
            if not missing or self.clock() >= due_time or force:
                kv_set(s, f"last_cycle:{tf}", want)
            kv_set(s, f"cycle_info:{tf}", {"at": self.clock(), "candle_ts": want, "missing": missing[:100],
                                           "missing_count": len(missing), "stale_count": stale,
                                           "errors": dict(list(out["errors"].items())[:20])})

        # Process automated trading on new breakout signals
        if self.trading_engine and result.get("signals", 0) > 0:
            try:
                await self._process_trading_signals(tf)
            except Exception as exc:
                log.warning("Automated trade processing error: %s", exc)

        self.phase = "idle"
        return {**result, "missing": len(missing), "stale": stale}

    async def fill_all_gaps(self, tf: str) -> int:
        filled = 0
        for coin in self._active():
            if await asyncio.to_thread(find_gaps, self.db, coin.id, tf):
                try:
                    filled += await fill_gaps(self.db, self.api, coin, tf, self.clock())
                except Exception as exc:                                  # noqa: BLE001
                    log.warning("gap fill %s %s failed: %s", coin.base, tf, exc)
        return filled

    # ---- what is due ----------------------------------------------------------------------------
    async def run_due(self) -> list[str]:
        """Run whatever is due now. Returns what ran. Safe to call as often as you like."""
        ran: list[str] = []
        async with self._lock:
            now = self.clock()
            with self.db.session() as s:
                universe_day = kv_get(s, "universe_day")
            have_coins = bool(await asyncio.to_thread(self._active))
            if not have_coins or universe_day != _day(now):
                await self.refresh_universe()
                ran.append("universe")
            for tf in self.settings.enabled_timeframes:
                now = self.clock()
                want = latest_closed(tf, now)
                due_time = want + TF_SECONDS[tf] + self.settings.scan_delay_s.get(tf, 300)
                with self.db.session() as s:
                    last = kv_get(s, f"last_cycle:{tf}")
                    seen_before = kv_get(s, f"cycle_info:{tf}") is not None
                first_ever = last is None and not seen_before
                if last is not None and last >= want:
                    continue
                if not first_ever and now < due_time:
                    continue
                await self.run_cycle(tf)
                ran.append(tf)
            if "1d" in self.settings.enabled_timeframes:
                with self.db.session() as s:
                    gaps_day = kv_get(s, "gaps_day")
                if gaps_day != _day(now) and "1d" in ran:
                    await self.fill_all_gaps("1d")
                    with self.db.session() as s:
                        kv_set(s, "gaps_day", _day(now))
                    ran.append("gaps")
            self.phase = "idle"
        return ran

    async def _process_trading_signals(self, tf: str) -> None:
        """Feed newly created breakout signals into the trading engine."""
        if not self.trading_engine or not self.trading_engine.settings.auto_trade:
            return
        from .scanner import list_scan
        scan_data = list_scan(self.db, tf)
        for item in scan_data.get("items", []):
            if item.get("direction") == "long":
                base = item.get("base")
                symbol = item.get("symbol")
                pattern = item.get("pattern")
                entry = item.get("entry")
                stop = item.get("stop")
                target = item.get("target")
                if base and symbol and entry and stop and entry > stop:
                    await self.trading_engine.process_signal(base, symbol, pattern, entry, stop, target=target)

    async def _update_open_positions_pnl(self) -> None:
        """Update PnL and check SL/TP for open positions using latest candle closes or tickers."""
        if not self.trading_engine:
            return
        opens = self.trading_engine.get_open_positions()
        if not opens:
            return
        open_bases = [p.base for p in opens]
        with self.db.session() as s:
            rows = s.execute(select(Coin.base, Coin.last_price).where(Coin.base.in_(open_bases))).all()
        prices = {b: lp for b, lp in rows if lp is not None and lp > 0}
        self.trading_engine.update_open_positions(prices)

    async def run_forever(self, every: float = 30.0) -> None:
        while True:
            try:
                await self.run_due()
                await self._update_open_positions_pnl()
            except asyncio.CancelledError:
                raise
            except Exception:                                              # noqa: BLE001 - the loop must survive
                log.exception("scheduler cycle failed; will try again")
                self.phase = "idle (last cycle failed)"
            await asyncio.sleep(every)

    # ---- health -------------------------------------------------------------------------------------
    def health(self) -> dict:
        with self.db.session() as s:
            rep = kv_get(s, "universe_report", {}) or {}
            total = s.scalar(select(func.count()).select_from(Coin).where(Coin.active.is_(True))) or 0
            tfs = {}
            for tf in self.settings.enabled_timeframes:
                info = kv_get(s, f"cycle_info:{tf}", {}) or {}
                run = s.scalar(select(ScanRun).where(ScanRun.tf == tf).order_by(ScanRun.id.desc()).limit(1))
                tfs[tf] = {
                    "last_cycle_candle": kv_get(s, f"last_cycle:{tf}"),
                    "latest_candle_ts": kv_get(s, f"latest_ts:{tf}", 0),
                    "last_scan_at": run.finished if run else None,
                    "last_scan": None if run is None else {"scanned": run.coins_scanned, "skipped": run.coins_skipped,
                                                           "signals": run.signals_created, "errors": run.errors},
                    "missing_latest_candle": info.get("missing_count", 0),
                    "missing_examples": (info.get("missing") or [])[:10],
                    "stale_coins": info.get("stale_count", 0),
                    "backfill": {"coins_with_candles": count_coins_with_candles(self.db, tf), "coins": total},
                }
        venue_err = {**{v: e for v, e in (rep.get("venue_errors") or {}).items()}, **self.venue_errors}
        return {
            "status": "ok", "data_source": self.settings.data_source, "phase": self.phase,
            "universe": {"coins": total, "wanted": self.settings.universe_size,
                         "markets_seen": rep.get("markets_seen"), "removed": rep.get("removed"),
                         "by_venue": rep.get("by_venue"), "tiers": rep.get("tiers"),
                         "venues_ok": rep.get("venues_ok"), "updated_at": rep.get("at")},
            "timeframes": tfs, "venue_errors": venue_err,
        }
