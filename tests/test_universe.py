import asyncio

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db import Candle, Coin, kv_get
from app.universe import apply_stable_rule, is_leveraged, refresh_universe, tier_for
from tests.conftest import END, DAY, add_coin, fixture_rows


def run(coro):
    return asyncio.run(coro)


def bases(db, active=True):
    with db.session() as s:
        q = select(Coin.base)
        if active:
            q = q.where(Coin.active.is_(True))
        return set(s.scalars(q))


def test_tiers():
    assert tier_for(20_000_000) == "A" and tier_for(19_999_999) == "B"
    assert tier_for(5_000_000) == "B" and tier_for(4_999_999) == "C"
    assert tier_for(1_000_000) == "C"


def test_junk_is_removed_and_real_coins_stay(db, demo, settings):
    rep = run(refresh_universe(db, demo, settings, sectors={}))
    got = bases(db)
    assert not got & {"USDC", "FDUSD", "TUSD", "DAI"}                      # stablecoins
    assert not got & {"BTCUP", "BTCDOWN", "ETHUP", "ETHDOWN"}              # leveraged tokens
    assert not got & {"WBTC", "STETH", "CBBTC"}                            # wrapped copies
    assert {"BTC", "ETH", "JUP", "SYRUP"} <= got                           # JUP / SYRUP only look leveraged
    assert rep.removed["stablecoin"] >= 4 and rep.removed["leveraged"] >= 4 and rep.removed["wrapped"] >= 3
    assert rep.selected == len(got) == 100                                  # capped at UNIVERSE_SIZE


def test_is_leveraged_needs_a_real_base_coin():
    allb = {"BTC", "ETH", "JUP", "SYR"}
    assert is_leveraged("BTCUP", allb) and is_leveraged("ETH3L", allb) and is_leveraged("BTCBEAR", allb)
    assert not is_leveraged("JUP", allb)        # "J" is not a coin
    assert not is_leveraged("SYRUP", {"BTC"})   # "SYR" not listed here
    assert not is_leveraged("UP", allb)


def test_500_coin_universe_from_several_exchanges(tmp_path, clock):
    from app.db import Database
    from app.demo import DemoExchange
    st = Settings(database_url=f"sqlite:///{tmp_path}/big.db")
    d = Database(st.database_url)
    d.create_all()
    ex = DemoExchange(n_coins=640, days=800, clock=clock)
    rep = run(refresh_universe(d, ex, st, sectors={}))
    assert rep.selected == 500
    assert len(rep.by_venue) == 5 and all(n > 40 for n in rep.by_venue.values())    # spread over 5 exchanges
    assert set(rep.tiers) == {"A", "B", "C"}
    with d.session() as s:
        vols = list(s.scalars(select(Coin.quote_volume).where(Coin.active.is_(True))))
        assert min(vols) >= st.min_quote_volume
        assert kv_get(s, "universe_report")["selected"] == 500


def test_volume_floor(db, demo, settings):
    rep = run(refresh_universe(db, demo, settings.with_(min_quote_volume=1.5e9), sectors={}))
    assert 0 < rep.selected < 100 and rep.removed["low_volume"] > 0


def test_each_coin_uses_exactly_one_exchange(db, demo, settings):
    run(refresh_universe(db, demo, settings, sectors={}))
    with db.session() as s:
        coins = list(s.scalars(select(Coin).where(Coin.active.is_(True))))
    assert len({c.base for c in coins}) == len(coins)          # base is unique
    # coins listed on two exchanges still got a single one
    dual = [c for c in coins if len(demo.coins[c.base].volumes) > 1]
    assert dual and all(c.venue in demo.coins[c.base].volumes for c in dual)


def test_chosen_exchange_is_sticky_and_a_switch_wipes_the_candles(db, demo, settings):
    run(refresh_universe(db, demo, settings, sectors={}))
    base = next(b for b, c in demo.coins.items() if len(c.volumes) > 1 and c.rank < 60 and c.kind == "random")
    with db.session() as s:
        coin = s.scalar(select(Coin).where(Coin.base == base))
    old = coin.venue
    other = next(v for v in demo.coins[base].volumes if v != old)
    demo.coins[base].volumes[other] = demo.coins[base].volumes[old] * 10        # other exchange now much bigger
    run(refresh_universe(db, demo, settings, sectors={}))
    with db.session() as s:
        assert s.scalar(select(Coin.venue).where(Coin.base == base)) == old     # stays put
    add_coin  # (helper imported for other tests)
    from app.candles import store_rows
    store_rows(db, coin.id, "1d", fixture_rows("range", 100))
    demo.dropped.add((old, base))                                               # delisted where it was
    rep = run(refresh_universe(db, demo, settings, sectors={}))
    with db.session() as s:
        assert s.scalar(select(Coin.venue).where(Coin.base == base)) == other
        assert s.scalar(select(Candle.ts).where(Candle.coin_id == coin.id)) is None      # history wiped
    assert rep.switched and rep.switched[0]["base"] == base


def test_failing_exchange_keeps_its_previous_coins(db, demo, settings):
    run(refresh_universe(db, demo, settings, sectors={}))
    before = bases(db)
    demo.fail_venues = {"okx"}
    rep = run(refresh_universe(db, demo, settings, sectors={}))
    assert "okx" in rep.venue_errors and "okx" not in rep.venues_ok
    assert bases(db) == before


def test_stable_price_rule(db, demo, settings):
    run(refresh_universe(db, demo, settings, sectors={}))
    assert "USDX" in bases(db)
    from app.candles import update_coin
    with db.session() as s:
        coin = s.scalar(select(Coin).where(Coin.base == "USDX"))
    run(update_coin(db, demo, coin, "1d", settings, demo.clock(), {}))
    assert apply_stable_rule(db) == ["USDX"]
    assert "USDX" not in bases(db)
    run(refresh_universe(db, demo, settings, sectors={}))                      # stays out on the next refresh
    assert "USDX" not in bases(db)


def test_sector_comes_from_the_sector_file(db, demo, settings):
    run(refresh_universe(db, demo, settings, sectors={"BTC": "Layer-1"}))
    with db.session() as s:
        assert s.scalar(select(Coin.sector).where(Coin.base == "BTC")) == "Layer-1"
        assert s.scalar(select(Coin.sector).where(Coin.base == "ETH")) == "Other"
