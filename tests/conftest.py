import os
import random
from datetime import datetime, timezone

import pytest

os.environ.setdefault("DATA_SOURCE", "demo")
os.environ.setdefault("DISABLE_SCHEDULER", "1")

from app.config import Settings          # noqa: E402
from app.db import Database               # noqa: E402
from app.demo import DemoExchange, pattern_series   # noqa: E402

DAY = 86_400
T0 = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc).timestamp()


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def settings(tmp_path):
    return Settings(data_source="demo", database_url=f"sqlite:///{tmp_path}/t.db", universe_size=100,
                    min_quote_volume=1_000_000, retry_interval_s=0, retry_max_s=0, demo_coins=120, demo_days=800)


@pytest.fixture()
def db(settings):
    d = Database(settings.database_url)
    d.create_all()
    yield d
    d.dispose()


@pytest.fixture()
def demo(clock, settings):
    return DemoExchange(n_coins=settings.demo_coins, days=settings.demo_days, clock=clock)


END = int(T0 // DAY) * DAY - DAY          # open time of the latest completed daily candle


def fixture_rows(kind, n, seed=0, **kw):
    return pattern_series(kind, n, END, DAY, random.Random(f"{kind}{seed}"), **kw)


def add_coin(db, base="AAA", rows=None, tf="1d", venue="binance", volume=3e7, sector="Other", tier=None):
    from app.candles import store_rows
    from app.db import Coin
    from app.universe import tier_for
    with db.session() as s:
        c = Coin(base=base, symbol=f"{base}/USDT", venue=venue, quote_volume=volume, last_price=1.0,
                 tier=tier or tier_for(volume), sector=sector, active=True)
        s.add(c)
        s.commit()
        cid = c.id
    if rows:
        store_rows(db, cid, tf, rows)
    return cid


def tail_rows(last_row, closes, vols=None, secs=DAY, wick=0.002):
    """Candles that follow last_row, one per close (open = previous close)."""
    rows, prev, ts = [], last_row[4], last_row[0]
    for j, c in enumerate(closes):
        ts += secs
        hi, lo = max(prev, c) * (1 + wick), min(prev, c) * (1 - wick)
        rows.append([ts, prev, hi, lo, c, (vols[j] if vols else 1000.0)])
        prev = c
    return rows
