# Decisions and honest limits

Choices made while building, so you can change them on purpose.

1. **Intraday timeframes only run W pattern and range breakout.** The 365-day and all-time-high patterns need
   a year or more of history, which is thousands of 1H candles. They run on 1D only.
2. **One exchange per coin (sticky).** A coin keeps the first exchange it was found on. Mixing exchanges
   would make candles jump. If a coin must move, its candles, signals and chains for that coin are wiped.
3. **Leveraged-token rule is global.** A name ending in UP, DOWN, BULL, BEAR, 2L-5L, 2S-5S is removed only when
   the part before it is itself a real coin on some exchange (BTCUP goes, JUP and SYRUP stay).
4. **Stable-price rule.** If 30 daily closes are all between 0.99 and 1.01, the coin is treated as a stablecoin.
5. **Volume rule is on the server** (breakout candle volume at least 1.5 times the 20-candle average). It is not a
   per-user switch, so every visitor sees the same signals.
6. **Coin names and sectors** come from `app/sectors.json`, written by hand (173 coins). Other coins show
   sector "Other". Add rows to the file to improve this.
7. **First scan is a replay.** On the first run each coin's last 1000 candles are replayed with the same code as
   the live scan, so old chains and the pattern statistics have history. Live scan and replay give the same
   result (there is a test for this).
8. **Breakouts must close beyond the level** and be fresh (the candle before was not already beyond it).
9. **Low-365 breakdown is a short warning only.** It never opens a chain.
10. **Outlier candles** (wick more than 50% from the previous close) are held until a second fetch gives the same
    values. A single bad tick cannot create a signal.

## Not tested
- Real exchange feeds. They cannot be reached from the build machine (all five exchanges were blocked there).
  Run `python scripts/check_live.py` on your own computer or server to test them. One real bug was found this way
  and fixed: CCXT calls Gate.io `gate`, not `gateio`. Everything was tested with the demo
  exchange and fake failures (rate limit, venue down, missing candles). Watch `/api/health` after deploy.
- Win-rate statistics are only as good as the replayed history. Treat small samples with care.
- Always-on hosting is required. Check Render's current database terms for the plan you choose.
