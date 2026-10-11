"""Candle storage: resumable backfill, incremental updates, validation, outlier and gap handling."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select

from .config import TF_SECONDS, Settings
from .db import Candle, Coin, Database, kv_get, kv_set
from .exchange import Exchange, Row

log = logging.getLogger("candles")
PAGE = 1000


def is_valid(r: Row) -> bool:
    """Basic sanity: finite positive prices, high/low really bound open/close."""
    try:
        _, o, h, l, c, v = r[:6]
        if not all(x == x and x not in (float("inf"), float("-inf")) for x in (o, h, l, c, v)):
            return False
        return min(o, h, l, c) > 0 and v >= 0 and h >= max(o, c) - 1e-12 and l <= min(o, c) + 1e-12
    except (TypeError, ValueError):
        return False


def _insert_ignore(s, rows: list[dict]) -> None:
    name = s.get_bind().dialect.name
    if name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:  # pragma: no cover
        s.add_all(Candle(**r) for r in rows)
        return
    s.execute(insert(Candle).on_conflict_do_nothing(), rows)


def store_rows(db: Database, coin_id: int, tf: str, rows: list[Row]) -> None:
    if not rows:
        return
    with db.session() as s:
        _insert_ignore(s, [dict(coin_id=coin_id, tf=tf, ts=int(r[0]), o=r[1], h=r[2], l=r[3], c=r[4], v=r[5])
                           for r in rows])
        s.commit()


def last_candle(db: Database, coin_id: int, tf: str):
    with db.session() as s:
        return s.execute(select(Candle.ts, Candle.c).where(Candle.coin_id == coin_id, Candle.tf == tf)
                         .order_by(Candle.ts.desc()).limit(1)).first()


def trim(db: Database, coin_id: int, tf: str, keep: int) -> None:
    if keep <= 0:
        return
    with db.session() as s:
        cutoff = s.scalar(select(Candle.ts).where(Candle.coin_id == coin_id, Candle.tf == tf)
                          .order_by(Candle.ts.desc()).offset(keep).limit(1))
        if cutoff is not None:
            s.execute(delete(Candle).where(Candle.coin_id == coin_id, Candle.tf == tf, Candle.ts <= cutoff))
            s.commit()


def latest_closed(tf: str, now: float) -> int:
    secs = TF_SECONDS[tf]
    return int(now // secs) * secs - secs


@dataclass
class UpdateResult:
    stored: int = 0
    invalid: int = 0
    held_outlier: bool = False


async def update_coin(db: Database, api: Exchange, coin: Coin, tf: str, settings: Settings, now: float,
                      outlier_seen: dict) -> UpdateResult:
    """Fetch what is missing for one coin and timeframe. The first call is a backfill, later ones only
    fetch the new candles. A crash part-way loses nothing: the next call starts after the last stored candle."""
    secs = TF_SECONDS[tf]
    res = UpdateResult()
    newest = latest_closed(tf, now)
    last = await asyncio.to_thread(last_candle, db, coin.id, tf)
    if last is None:
        keep = settings.retention.get(tf, 0)
        if keep:
            since = newest - (keep - 1) * secs
        else:
            max_candles = max(settings.replay_candles, 1000)
            since = max((settings.history_start_ts // secs) * secs, newest - (max_candles - 1) * secs)
        prev_close = None
    else:
        since, prev_close = last[0] + secs, last[1]
    m = settings.params.outlier_move
    while since <= newest:
        rows = await api.ohlcv(coin.venue, coin.symbol, tf, since, PAGE)
        if not rows:
            break
        good: list[Row] = []
        stop = False
        for r in sorted(rows, key=lambda x: x[0]):
            ts = int(r[0])
            if ts < since:
                continue
            if ts + secs > now:                       # the candle still forming: never use it
                break
            if not is_valid(r):
                res.invalid += 1
                log.warning("%s %s: rejected invalid candle at %s", coin.base, tf, ts)
                continue
            if prev_close and (r[2] > prev_close * (1 + m) or r[3] < prev_close * (1 - m)):
                key = (coin.id, tf, ts)
                sig = tuple(round(x, 10) for x in r[1:6])
                if outlier_seen.get(key) != sig:       # first sighting: wait for a second fetch to agree
                    outlier_seen[key] = sig
                    res.held_outlier = True
                    stop = True
                    log.warning("%s %s: holding outlier candle at %s until a second fetch confirms it", coin.base, tf, ts)
                    break
                outlier_seen.pop(key, None)
            good.append(r)
            prev_close = r[4]
        if good:
            await asyncio.to_thread(store_rows, db, coin.id, tf, good)
            res.stored += len(good)
            since = int(good[-1][0]) + secs
        if stop or not good:
            break
    if res.stored:
        await asyncio.to_thread(trim, db, coin.id, tf, settings.retention.get(tf, 0))
    return res


async def update_all(db: Database, api: Exchange, coins: list[Coin], tf: str, settings: Settings, now: float,
                     outlier_seen: dict, concurrency: int = 24) -> dict:
    """Update every coin. One failing coin never stops the others."""
    sem = asyncio.Semaphore(concurrency)
    errors: dict[str, str] = {}
    stored = 0

    async def one(c: Coin):
        nonlocal stored
        async with sem:
            try:
                r = await update_coin(db, api, c, tf, settings, now, outlier_seen)
                stored += r.stored
                while r.held_outlier:
                    r = await update_coin(db, api, c, tf, settings, now, outlier_seen)
                    stored += r.stored
            except Exception as exc:                    # noqa: BLE001
                errors[c.base] = f"{type(exc).__name__}: {exc}"[:160]

    await asyncio.gather(*(one(c) for c in coins))
    return {"stored": stored, "errors": errors}


# ---- gaps ----------------------------------------------------------------------------------
def find_gaps(db: Database, coin_id: int, tf: str) -> list[tuple[int, int]]:
    """(last candle before the hole, first candle after it) for every hole inside stored history."""
    secs = TF_SECONDS[tf]
    with db.session() as s:
        ts = list(s.scalars(select(Candle.ts).where(Candle.coin_id == coin_id, Candle.tf == tf).order_by(Candle.ts)))
    return [(a, b) for a, b in zip(ts, ts[1:]) if b - a > secs]


async def fill_gaps(db: Database, api: Exchange, coin: Coin, tf: str, now: float) -> int:
    """Try once to refill each hole. Holes the exchange cannot fill (no trades that period) are
    remembered so they are not asked for again every day."""
    secs = TF_SECONDS[tf]
    key = f"gaps_tried:{coin.id}:{tf}"
    with db.session() as s:
        tried = set(kv_get(s, key, []))
    filled = 0
    gaps = await asyncio.to_thread(find_gaps, db, coin.id, tf)
    for a, b in gaps:
        if a in tried:
            continue
        rows = await api.ohlcv(coin.venue, coin.symbol, tf, a + secs, min(PAGE, (b - a) // secs))
        good = [r for r in rows if a < int(r[0]) < b and int(r[0]) + secs <= now and is_valid(r)]
        await asyncio.to_thread(store_rows, db, coin.id, tf, good)
        filled += len(good)
        tried.add(a)
    with db.session() as s:
        kv_set(s, key, sorted(tried))
    return filled


def count_coins_with_candles(db: Database, tf: str) -> int:
    with db.session() as s:
        return s.scalar(select(func.count(func.distinct(Candle.coin_id))).where(Candle.tf == tf)) or 0
