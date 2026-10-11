"""Settings. Everything comes from environment variables (the app does not read a .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

TF_SECONDS = {"1d": 86_400, "4h": 14_400, "1h": 3_600}

# Stablecoins and fiat-pegged coins: never scanned.
STABLES = frozenset({
    "USDT", "USDC", "FDUSD", "TUSD", "DAI", "USDE", "USDD", "USDP", "BUSD", "PYUSD", "USDS", "GUSD",
    "LUSD", "FRAX", "USTC", "USD1", "RLUSD", "EURC", "EURT", "AEUR", "EUR", "GBP", "TRY", "BRL",
    "UST", "SUSD", "CUSD", "USDJ", "XAUT", "PAXG",
})

# Wrapped / staked copies of another coin: they just follow the original.
WRAPPED = frozenset({
    "WBTC", "WETH", "STETH", "WSTETH", "CBBTC", "CBETH", "RETH", "WBETH", "BETH", "WEETH", "EETH",
    "METH", "SOLVBTC", "TBTC", "LBTC", "FBTC", "WBNB", "WSOL", "MSOL", "JITOSOL", "BNSOL", "WAVAX",
    "WTRX", "STSOL", "RENBTC", "HBTC", "BTCB", "WEOS", "STKAAVE",
})

# Leveraged-token suffixes. A name only counts as leveraged when what is left after removing the
# suffix is itself a coin listed on the same exchange (BTCUP -> BTC, but JUP -> J is not).
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR", "2L", "2S", "3L", "3S", "4L", "4S", "5L", "5S")


@dataclass(frozen=True)
class PatternParams:
    """Every pattern threshold lives here so it can be tuned in one place."""
    volume_mult: float = 1.5          # breakout volume must be >= this x the 20-candle average
    stop_atr: float = 1.0             # long breakout stop = level - stop_atr * ATR
    rr_target: float = 2.0            # default target = entry + rr_target * risk
    ath_min_candles: int = 180
    long_window: int = 365            # "365-day" high / low (daily candles)
    # W pattern (double bottom)
    w_min_gap: int = 10
    w_max_gap: int = 60
    w_low_tol: float = 0.03           # the two lows must be within 3% of each other
    w_min_depth: float = 0.05         # neckline at least 5% (or 2 ATR) above the lows
    w_depth_atr: float = 2.0
    w_stop_atr: float = 0.5
    w_max_age: int = 40               # the second low must be at most this many candles old
    w_undercut: float = 0.01          # no lower low than 1% below the pattern lows
    w_max_pivots: int = 6
    # Range breakout
    range_min_len: int = 20
    range_max_len: int = 60
    range_atr_mult: float = 6.0       # range height <= 6 ATR
    range_touch_atr: float = 0.25     # a "touch" is within 0.25 ATR of the boundary
    range_min_touches: int = 2
    range_stop_atr: float = 0.5
    # Breakout chain
    chain_stop_atr: float = 0.5       # trailing stop = latest swing low - 0.5 ATR
    # Candle checks
    outlier_move: float = 0.5         # a candle wick more than 50% from the neighbour is suspicious


# Which patterns run on which timeframe. The long-lookback ones (all-time high, 365-day
# high/low) are daily concepts, so intraday timeframes only run W and range.
TF_PATTERNS = {
    "1d": ("ath", "high365", "low365", "w", "range"),
    "4h": ("w", "range"),
    "1h": ("w", "range"),
}
PIVOT_K = {"1d": 3, "4h": 4, "1h": 4}

PATTERN_NAMES = {
    "ath": "All-time high breakout",
    "high365": "365-day high breakout",
    "low365": "365-day low breakdown",
    "w": "W pattern",
    "range": "Range breakout",
}
SECTORS = ("Layer-1", "Layer-2", "DeFi", "AI", "Meme", "Gaming", "Exchange", "Infrastructure", "Other")


def _env_list(name: str, default: str) -> tuple[str, ...]:
    raw = os.environ.get(name, default)
    return tuple(x.strip().lower() for x in raw.split(",") if x.strip())


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_num(name: str, default: float, cast=float):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return cast(raw)
    except ValueError as exc:
        raise SystemExit(f"Environment variable {name} must be a number, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    data_source: str = "live"                                  # "live" or "demo"
    database_url: str = "sqlite:///./zerocoin.db"
    venues: tuple[str, ...] = ("binance", "bybit", "okx", "kucoin", "gateio")
    universe_size: int = 500
    min_quote_volume: float = 1_000_000.0
    enabled_timeframes: tuple[str, ...] = ("1d",)
    require_volume: bool = True
    admin_token: str = ""
    rate_limit_per_min: int = 120
    disable_scheduler: bool = False
    replay_candles: int = 1000                                 # how far back the first scan looks
    retry_interval_s: float = 120.0                            # wait between tries for a missing candle
    retry_max_s: float = 1800.0                                # give up waiting after this long
    scan_delay_s: dict = field(default_factory=lambda: {"1d": 300, "4h": 300, "1h": 120})
    history_start_ts: int = 1_501_545_600                      # 2017-08-01, before any listed coin
    retention: dict = field(default_factory=lambda: {"1d": 0, "4h": 1500, "1h": 1000})  # 0 = keep all
    per_venue_concurrency: int = 4
    demo_coins: int = 640
    demo_days: int = 800
    params: PatternParams = field(default_factory=PatternParams)

    def with_(self, **kw) -> "Settings":
        return replace(self, **kw)


def load_settings() -> Settings:
    tfs = _env_list("ENABLED_TIMEFRAMES", "1d")
    bad = [t for t in tfs if t not in TF_SECONDS]
    if bad:
        raise SystemExit(f"ENABLED_TIMEFRAMES has unknown timeframe(s): {bad}. Use 1d, 4h, 1h.")
    source = os.environ.get("DATA_SOURCE", "live").strip().lower()
    if source not in ("live", "demo"):
        raise SystemExit("DATA_SOURCE must be 'live' or 'demo'")
    base = Settings()
    return Settings(
        data_source=source,
        database_url=os.environ.get("DATABASE_URL", base.database_url),
        venues=_env_list("UNIVERSE_VENUES", "binance,bybit,kucoin,gateio"),
        universe_size=_env_num("UNIVERSE_SIZE", base.universe_size, int),
        min_quote_volume=_env_num("MIN_QUOTE_VOLUME", base.min_quote_volume),
        enabled_timeframes=tfs,
        require_volume=_env_bool("REQUIRE_VOLUME", True),
        admin_token=os.environ.get("ADMIN_TOKEN", ""),
        rate_limit_per_min=_env_num("RATE_LIMIT_PER_MIN", base.rate_limit_per_min, int),
        disable_scheduler=_env_bool("DISABLE_SCHEDULER", False),
        replay_candles=_env_num("REPLAY_CANDLES", base.replay_candles, int),
        demo_coins=_env_num("DEMO_COINS", base.demo_coins, int),
        demo_days=_env_num("DEMO_DAYS", base.demo_days, int),
        params=PatternParams(volume_mult=base.params.volume_mult),
    )
