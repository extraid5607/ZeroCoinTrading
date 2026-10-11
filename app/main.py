"""ZeroCoin web app: a read-only JSON API, the website, and the background scanner."""
from __future__ import annotations

import asyncio
import hmac
import logging
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import market, queries
from .config import PATTERN_NAMES, SECTORS, TF_SECONDS, Settings, load_settings
from .db import Database
from .scanner import list_scan
from .scheduler import Pipeline

log = logging.getLogger("zerocoin")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

TF = Literal["1d", "4h", "1h"]
PatternId = Literal["ath", "high365", "low365", "w", "range"]
SectorName = Literal[SECTORS]                      # type: ignore[valid-type]

STATIC = Path(__file__).resolve().parent.parent / "static"
BASE_RE = re.compile(r"^[A-Za-z0-9]{1,15}$")
DISCLAIMER = "Educational tool, not financial advice. Crypto is risky."
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
       "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
       "form-action 'self'")


class RateLimiter:
    """Per-IP sliding window, kept in memory (the app runs as one process)."""
    def __init__(self, per_minute: int) -> None:
        self.limit = per_minute
        self.hits: dict[str, deque] = defaultdict(deque)
        self._last_sweep = 0.0

    def check(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        q = self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        if now - self._last_sweep > 300:               # forget idle visitors
            self._last_sweep = now
            for k in [k for k, v in self.hits.items() if not v or now - v[-1] > 60]:
                del self.hits[k]
        return True


def create_app(settings: Settings | None = None, api=None, clock=time.time) -> FastAPI:
    settings = settings or load_settings()
    db = Database(settings.database_url)
    limiter = RateLimiter(settings.rate_limit_per_min)
    state: dict = {"pipeline": None, "task": None}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.create_all()
        exchange = api
        if exchange is None:
            if settings.data_source == "demo":
                from .demo import DemoExchange
                exchange = DemoExchange(n_coins=settings.demo_coins, days=settings.demo_days, venues=settings.venues)
            else:
                from .exchange import CcxtExchange
                exchange = CcxtExchange(settings.per_venue_concurrency)
        from .trading_engine import TradingEngine
        trading_engine = TradingEngine(db)
        pipeline = Pipeline(db, exchange, settings, clock=clock, trading_engine=trading_engine)
        from .universe import load_sectors
        pipeline.sectors = {**load_sectors(), **getattr(exchange, "sectors", {})}
        state["pipeline"] = pipeline
        state["trading_engine"] = trading_engine
        if not settings.disable_scheduler:
            state["task"] = asyncio.create_task(pipeline.run_forever())
        try:
            yield
        finally:
            if state["task"]:
                state["task"].cancel()
                await asyncio.gather(state["task"], return_exceptions=True)
            await exchange.close()
            db.dispose()

    app = FastAPI(title="ZeroCoin", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.db, app.state.settings, app.state.limiter, app.state.runtime = db, settings, limiter, state

    # ---- middleware --------------------------------------------------------------------------------
    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            ip = request.client.host if request.client else "?"
            if not limiter.check(ip):
                return JSONResponse({"detail": "Too many requests. Wait a minute and try again."}, status_code=429,
                                    headers={"Retry-After": "30"})
        resp = await call_next(request)
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.url.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.exception_handler(RequestValidationError)
    async def bad_input(_req: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        loc = [str(x) for x in first.get("loc", []) if x not in ("query", "path")]
        return JSONResponse({"detail": f"Check {', '.join(loc) or 'the request'}: {first.get('msg', 'invalid value')}."},
                            status_code=422)

    # ---- helpers -----------------------------------------------------------------------------------
    def need_tf(tf: str) -> str:
        if tf not in settings.enabled_timeframes:
            raise HTTPException(422, f"Timeframe {tf} is not enabled. Available: {', '.join(settings.enabled_timeframes)}.")
        return tf

    def need_coin(base: str):
        if not BASE_RE.match(base):
            raise HTTPException(404, "Coin not found.")
        coin = queries.find_coin(db, base)
        if coin is None:
            raise HTTPException(404, "Coin not found.")
        return coin

    # ---- routes ------------------------------------------------------------------------------------
    @app.get("/api/health")
    def health():
        pipeline = state["pipeline"]
        if pipeline is None:
            return {"status": "starting"}
        h = pipeline.health()
        h["universe"]["goal_met"] = h["universe"]["coins"] >= settings.universe_size
        return h

    @app.get("/api/meta")
    def meta():
        return {"app": "ZeroCoin", "data_source": settings.data_source, "is_live": (settings.data_source == "live"),
                "timeframes": list(settings.enabled_timeframes),
                "patterns": PATTERN_NAMES, "sectors": list(SECTORS), "tiers": ["A", "B", "C"],
                "require_volume": settings.require_volume, "disclaimer": DISCLAIMER}

    @app.get("/api/scan")
    def scan(tf: TF = "1d", pattern: PatternId | None = None,
             tier: Literal["A", "B", "C"] | None = None,
             sector: SectorName | None = None,
             q: str | None = Query(None, max_length=15, pattern=r"^[A-Za-z0-9]*$")):
        need_tf(tf)
        return list_scan(db, tf, pattern, tier, sector, q)

    @app.post("/api/scan/trigger")
    async def trigger_scan(tf: TF = "1d"):
        need_tf(tf)
        pipeline = state["pipeline"]
        if pipeline is None:
            raise HTTPException(503, "Scanner not ready.")
        asyncio.create_task(pipeline.run_due())
        return {"status": "scanning_triggered", "tf": tf}

    @app.get("/api/watchlist")
    def watchlist(bases: str = Query("", max_length=700, pattern=r"^[A-Za-z0-9,]*$")):
        """Detail for a handful of saved coins in one call (the watchlist itself lives in the browser)."""
        wanted = list(dict.fromkeys(b.upper() for b in bases.split(",") if b))[:60]
        items, missing = [], []
        for base in wanted:
            c = queries.find_coin(db, base) if BASE_RE.match(base) else None
            if c is None:
                missing.append(base)
            else:
                items.append(queries.coin_detail(db, c, settings.enabled_timeframes))
        return {"items": items, "missing": missing}

    @app.get("/api/coins/{base}")
    def coin(base: str):
        c = need_coin(base)
        return queries.coin_detail(db, c, settings.enabled_timeframes)

    @app.get("/api/coins/{base}/live")
    async def coin_live(base: str):
        c = need_coin(base)
        pipeline = state["pipeline"]
        if pipeline and hasattr(pipeline.api, "fetch_ticker"):
            try:
                t = await pipeline.api.fetch_ticker(c.venue, c.symbol)
                return {"status": "ok", "base": c.base, "venue": c.venue, "symbol": c.symbol, **t}
            except Exception as exc:
                log.debug("Live ticker fetch failed for %s: %s", c.base, exc)
        return {"status": "fallback", "base": c.base, "venue": c.venue, "symbol": c.symbol, "last": c.last_price}

    @app.get("/api/coins/{base}/candles")
    def candles(base: str, tf: TF = "1d", limit: int = Query(300, ge=20, le=1500)):
        need_tf(tf)
        c = need_coin(base)
        return {"base": c.base, "tf": tf, "secs": TF_SECONDS[tf], "candles": queries.coin_candles(db, c, tf, limit)}

    @app.get("/api/signals/{base}")
    def signals(base: str, tf: TF = "1d"):
        need_tf(tf)
        return queries.coin_signals(db, need_coin(base), tf)

    @app.get("/api/market")
    def market_state():
        return market.btc_state(db)

    @app.get("/api/sectors")
    def sectors(period: Literal["day", "week", "month"] = "day"):
        return market.sector_heatmap(db, period)

    @app.get("/api/stats/patterns")
    def stats(tf: TF = "1d"):
        need_tf(tf)
        return market.pattern_stats(db, tf)

    # ---- automated trading & delta exchange ----------------------------------------
    def _engine():
        eng = state.get("trading_engine")
        if eng is None:
            from .trading_engine import TradingEngine
            eng = TradingEngine(db)
            state["trading_engine"] = eng
        return eng

    @app.get("/api/trading/overview")
    async def trading_overview():
        engine = _engine()
        overview = await engine.calculate_overview()
        opens = [p.to_dict() for p in engine.get_open_positions()]
        closed = [p.to_dict() for p in engine.get_closed_positions(30)]
        return {
            "status": "ok",
            "overview": overview,
            "open_positions": opens,
            "closed_positions": closed,
        }

    @app.post("/api/trading/toggle")
    async def trading_toggle(request: Request):
        engine = _engine()
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        target = body.get("enabled") if isinstance(body, dict) else None
        active = engine.toggle_auto_trade(target)
        if active:
            pipeline = state.get("pipeline")
            if pipeline:
                for tf in settings.enabled_timeframes:
                    try:
                        await pipeline._process_trading_signals(tf)
                    except Exception as e:
                        log.warning("Immediate signal processing on toggle failed for %s: %s", tf, e)
        return {"status": "ok", "auto_trade": active}

    @app.post("/api/trading/config")
    async def trading_config(request: Request):
        engine = _engine()
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON payload.")
        allowed = {"mode", "region", "risk_per_trade_pct", "max_positions", "leverage"}
        updates = {k: v for k, v in payload.items() if k in allowed}
        engine.update_config(**updates)
        return {"status": "ok", "settings": engine.settings.to_dict()}

    @app.post("/api/trading/positions/{pos_id}/close")
    def trading_close_position(pos_id: int):
        engine = _engine()
        success = engine.close_position(pos_id)
        if not success:
            raise HTTPException(404, "Position not found or already closed.")
        return {"status": "ok", "closed_id": pos_id}

    @app.post("/api/trading/positions/close_all")
    def trading_close_all():
        engine = _engine()
        count = engine.close_all_positions()
        return {"status": "ok", "closed_count": count}

    @app.post("/api/trading/paper/reset")
    def trading_paper_reset():
        engine = _engine()
        bal = engine.reset_paper_wallet()
        return {"status": "ok", "balance": bal}

    if settings.admin_token:                           # without a token this route does not exist
        @app.post("/api/admin/rescan")
        async def rescan(request: Request, tf: TF = "1d"):
            given = request.headers.get("x-admin-token", "")
            if not hmac.compare_digest(given.encode(), settings.admin_token.encode()):
                raise HTTPException(403, "Forbidden.")
            need_tf(tf)
            pipeline = state["pipeline"]
            asyncio.create_task(pipeline.run_cycle(tf, force=True))
            return JSONResponse({"status": "started", "tf": tf}, status_code=202)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
