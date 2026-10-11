# ZeroCoin - Real-Time Live Crypto Scanner (PowerShell Launcher)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "     ZeroCoin - Real-Time Live Crypto Scanner      " -ForegroundColor Cyan
Write-Host "             Powered by CCXT Multi-Exchange        " -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Check Python
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Host "[ERROR] Python was not found in PATH. Please install Python 3.10+." -ForegroundColor Red
    pause
    exit 1
}

# 2. Check dependencies
try {
    python -c "import fastapi, uvicorn, ccxt, sqlalchemy" 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Missing packages" }
} catch {
    Write-Host "[INFO] Installing required dependencies..." -ForegroundColor Yellow
    python -m pip install -r requirements.txt
}

# 3. Launch Live Real-Time Data Scanner
$env:DATA_SOURCE = "live"
Write-Host "[LIVE] Starting Real-Time CCXT Data Scanner..." -ForegroundColor Green
Write-Host "Exchanges: Binance, Bybit, KuCoin, Gate.io" -ForegroundColor Gray
Write-Host "Opening http://localhost:8000 in your browser..." -ForegroundColor Cyan
Write-Host ""

Start-Process "http://localhost:8000"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
