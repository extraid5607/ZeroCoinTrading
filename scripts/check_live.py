"""Check that the real exchanges work from THIS machine, before you rely on the scanner.

    python scripts/check_live.py

For each exchange it loads the spot USDT markets and downloads the last daily candles of BTC.
If an exchange fails with 451 / 403 / "restricted location", that exchange blocks the country your
server is in. Use a different server region (Render: Frankfurt) or remove that venue from UNIVERSE_VENUES.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Settings  # noqa: E402
from app.exchange import CcxtExchange  # noqa: E402


async def check(api: CcxtExchange, venue: str) -> bool:
    t0 = time.time()
    try:
        markets = await api.list_markets(venue)
        if not markets:
            print(f"FAIL {venue}: connected but no USDT spot markets with volume")
            return False
        liquid = [m for m in markets if m.quote_volume >= 1_000_000]
        btc = next((m for m in markets if m.base == "BTC"), None)
        if btc is None:
            print(f"FAIL {venue}: no BTC/USDT market")
            return False
        since = int(time.time()) - 10 * 86400
        rows = await api.ohlcv(venue, btc.symbol, "1d", since, 20)
        if len(rows) < 5:
            print(f"FAIL {venue}: only {len(rows)} daily BTC candles came back")
            return False
        print(f"OK   {venue}: {len(markets)} USDT markets, {len(liquid)} above $1M a day, "
              f"BTC {rows[-1][4]:,.0f}, {time.time() - t0:.1f}s")
        return True
    except Exception as exc:  # noqa: BLE001 - this is a diagnostic tool
        print(f"FAIL {venue}: {type(exc).__name__}: {str(exc)[:160]}")
        return False


async def main() -> int:
    api = CcxtExchange()
    try:
        results = await asyncio.gather(*[check(api, v) for v in Settings().venues])
    finally:
        await api.close()
    ok = sum(results)
    print(f"\n{ok} of {len(results)} exchanges work from here.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
