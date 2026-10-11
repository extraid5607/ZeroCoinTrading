# ZeroCoin - Live Real-Time Crypto Pattern Scanner

Real-time crypto pattern scanner powered by [CCXT](https://github.com/ccxt/ccxt). Scans 500+ top USDT spot coins across major exchanges (Binance, Bybit, KuCoin, Gate.io, OKX) and detects five technical breakout patterns in real time:
- **All-Time-High (ATH) Breakout**
- **365-Day High Breakout**
- **365-Day Low Breakdown** (risk warning)
- **W Pattern (Double Bottom)**
- **Range Breakout**

*Educational tool, not financial advice. Crypto is risky.*

---

## ⚡ Quick Start (One-Click)

On Windows, simply double-click **`start.bat`** (or run `.\start.ps1` in PowerShell):
1. Automatically verifies Python 3.10+ and installs dependencies.
2. Connects directly to live crypto exchanges via CCXT.
3. Automatically launches `http://localhost:8000` in your web browser.

---

## 🛠️ Run from Terminal

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. (Optional) Check live exchange connectivity from your location
python scripts/check_live.py

# 3. Launch live real-time server
uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Open `http://localhost:8000`. You can monitor real-time scanner phases via `http://localhost:8000/api/health`.

---

## 🧪 Run Automated Tests

```bash
python -m pytest
```

---

## ☁️ Deploy on Render

1. Push this folder to a GitHub repository.
2. In Render: **New > Blueprint** > select your repo. `render.yaml` creates the web service and linked PostgreSQL database automatically.
3. Keep it to one worker: the scanner pipeline runs in the background of the web process.

---

## ⚙️ Configuration & Settings

See `.env.example` for available environment variables:
- `DATA_SOURCE`: Set to `live` (default) for real-time exchange data.
- `UNIVERSE_VENUES`: Exchanges to query (`binance,bybit,kucoin,gateio,okx`).
- `ENABLED_TIMEFRAMES`: Defaults to `1d`. Can be set to `1d,4h,1h`.
- `UNIVERSE_SIZE`: Number of top liquid coins to track (default: 500).
