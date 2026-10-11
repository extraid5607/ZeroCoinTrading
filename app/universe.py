"""The coin list: which 500+ coins are scanned, and on which exchange each one lives."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlalchemy import delete, func, select

from .config import LEVERAGED_SUFFIXES, STABLES, WRAPPED, Settings
from .db import Candle, Chain, Coin, Database, ScanState, Signal, kv_set
from .exchange import Exchange, MarketInfo

log = logging.getLogger("universe")

TIER_A = 20_000_000
TIER_B = 5_000_000
_NAME_OK = re.compile(r"^[A-Z0-9]{2,15}$")


def tier_for(volume: float) -> str:
    return "A" if volume >= TIER_A else "B" if volume >= TIER_B else "C"


def _load_coin_file() -> dict:
    path = Path(__file__).with_name("sectors.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k.upper(): v for k, v in data.items() if not k.startswith("_")}


def load_sectors() -> dict[str, str]:
    """base -> sector from sectors.json. An entry is "Sector" or ["Sector", "Coin name"]."""
    out = {}
    for k, v in _load_coin_file().items():
        sector = v[0] if isinstance(v, list) and v else v
        if isinstance(sector, str):
            out[k] = sector
    return out


def load_names() -> dict[str, str]:
    """base -> display name, for entries written as ["Sector", "Coin name"]."""
    return {k: v[1] for k, v in _load_coin_file().items() if isinstance(v, list) and len(v) > 1}


def is_leveraged(base: str, all_bases: set[str]) -> bool:
    """BTCUP / ETH3L style tokens: a known coin plus a leverage suffix."""
    for suf in LEVERAGED_SUFFIXES:
        if base.endswith(suf) and len(base) > len(suf) and base[: -len(suf)] in all_bases:
            return True
    return False


@dataclass
class UniverseReport:
    venues_ok: list = field(default_factory=list)
    venue_errors: dict = field(default_factory=dict)
    markets_seen: int = 0
    removed: dict = field(default_factory=lambda: {"stablecoin": 0, "wrapped": 0, "leveraged": 0, "bad_name": 0,
                                                    "low_volume": 0, "outside_top": 0})
    selected: int = 0
    wanted: int = 0
    by_venue: dict = field(default_factory=dict)
    tiers: dict = field(default_factory=dict)
    switched: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _wipe_coin_data(s, coin_id: int) -> None:
    for table in (Signal, Chain, ScanState, Candle):
        s.execute(delete(table).where(table.coin_id == coin_id))


async def refresh_universe(db: Database, api: Exchange, settings: Settings, sectors: dict[str, str] | None = None,
                           now: float | None = None) -> UniverseReport:
    import time as _time
    now = now if now is not None else _time.time()
    sectors = sectors if sectors is not None else load_sectors()
    rep = UniverseReport(wanted=settings.universe_size)

    # 1. markets from every exchange; a failing exchange keeps its previous coins
    with db.session() as s:
        previous = list(s.scalars(select(Coin)))
    candidates: list[MarketInfo] = []
    for venue in settings.venues:
        try:
            got = await api.list_markets(venue)
            rep.venues_ok.append(venue)
            candidates += got
        except Exception as exc:                       # noqa: BLE001 - one bad exchange must not stop the rest
            rep.venue_errors[venue] = f"{type(exc).__name__}: {exc}"[:160]
            log.warning("universe: %s failed (%s); keeping its previous coins", venue, exc)
            candidates += [MarketInfo(c.venue, c.base, c.symbol, c.quote_volume, c.last_price)
                           for c in previous if c.venue == venue and c.active]
    rep.markets_seen = len(candidates)

    # 2. remove stablecoins, wrapped copies, leveraged tokens
    all_bases = {m.base.upper() for m in candidates}
    options: dict[str, dict[str, MarketInfo]] = {}
    for m in candidates:
        base = m.base.upper()
        if not _NAME_OK.match(base):
            rep.removed["bad_name"] += 1
        elif base in STABLES:
            rep.removed["stablecoin"] += 1
        elif base in WRAPPED:
            rep.removed["wrapped"] += 1
        elif is_leveraged(base, all_bases):
            rep.removed["leveraged"] += 1
        else:
            options.setdefault(base, {})[m.venue] = m

    # 3. one exchange per coin, and it sticks
    by_base = {c.base: c for c in previous}
    chosen: dict[str, MarketInfo] = {}
    for base, per_venue in options.items():
        old = by_base.get(base)
        if old is not None and old.note == "stable price":
            continue                                    # already caught by the price rule
        if old is not None and old.venue in per_venue:
            chosen[base] = per_venue[old.venue]          # sticky: stay where we are
        else:
            chosen[base] = max(per_venue.values(), key=lambda x: x.quote_volume)

    # 4. volume floor and size cap
    ranked = sorted(chosen.values(), key=lambda m: m.quote_volume, reverse=True)
    liquid = [m for m in ranked if m.quote_volume >= settings.min_quote_volume]
    rep.removed["low_volume"] = len(ranked) - len(liquid)
    picked = liquid[: settings.universe_size]
    rep.removed["outside_top"] = len(liquid) - len(picked)
    picked_bases = {m.base.upper() for m in picked}

    # 5. save
    with db.session() as s:
        existing = {c.base: c for c in s.scalars(select(Coin))}
        for m in picked:
            base = m.base.upper()
            coin = existing.get(base)
            if coin is None:
                coin = Coin(base=base, symbol=m.symbol, venue=m.venue)
                s.add(coin)
                s.flush()
            elif coin.venue != m.venue:
                rep.switched.append({"base": base, "from": coin.venue, "to": m.venue})
                _wipe_coin_data(s, coin.id)              # never mix two exchanges in one history
                coin.venue = m.venue
            coin.symbol = m.symbol
            coin.quote_volume = m.quote_volume
            coin.last_price = m.last_price
            coin.tier = tier_for(m.quote_volume)
            coin.sector = sectors.get(base, "Other")
            coin.active = True
            coin.note = ""
            coin.updated = now
        for base, coin in existing.items():
            if base in picked_bases:
                continue
            coin.active = False
            if coin.note != "stable price":
                coin.note = ("filtered" if base not in options else
                             "below volume floor" if base in chosen and chosen[base].quote_volume < settings.min_quote_volume
                             else "outside top list" if base in chosen else "no longer listed")
        s.commit()
        rows = list(s.execute(select(Coin.venue, Coin.tier, func.count()).where(Coin.active.is_(True))
                              .group_by(Coin.venue, Coin.tier)))
    for venue, tier, n in rows:
        rep.by_venue[venue] = rep.by_venue.get(venue, 0) + n
        rep.tiers[tier] = rep.tiers.get(tier, 0) + n
    rep.selected = sum(rep.by_venue.values())
    with db.session() as s:
        kv_set(s, "universe_report", {**rep.as_dict(), "at": now})
    log.info("universe: %d coins (wanted %d), removed %s", rep.selected, rep.wanted, rep.removed)
    return rep


def apply_stable_rule(db: Database, tf: str = "1d", days: int = 30) -> list[str]:
    """Deactivate any coin whose daily closes all stayed within 1% of $1 for the last 30 days."""
    caught = []
    with db.session() as s:
        for coin in s.scalars(select(Coin).where(Coin.active.is_(True))):
            closes = list(s.scalars(select(Candle.c).where(Candle.coin_id == coin.id, Candle.tf == tf)
                                    .order_by(Candle.ts.desc()).limit(days)))
            if len(closes) >= days and all(0.99 <= c <= 1.01 for c in closes):
                coin.active = False
                coin.note = "stable price"
                caught.append(coin.base)
        s.commit()
    if caught:
        log.info("stable-price rule removed: %s", ", ".join(caught))
    return caught
