@echo off
setlocal enabledelayedexpansion
title ZeroCoin - Real-Time Crypto Pattern Scanner (CCXT)

cd /d "%~dp0"

echo ===================================================
echo     ZeroCoin - Real-Time Live Crypto Scanner
echo             Powered by CCXT Multi-Exchange
echo ===================================================
echo.

:: 1. Check Python installation
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python was not found in your PATH.
    echo Please install Python 3.10 or higher and enable "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

:: 2. Check essential dependencies
python -c "import fastapi, uvicorn, ccxt, sqlalchemy" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [INFO] Installing required dependencies...
    python -m pip install -r requirements.txt
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
    echo [OK] Dependencies installed successfully.
    echo.
)

:: 3. Launch Live Real-Time Data Scanner directly
echo [LIVE] Starting Real-Time CCXT Data Scanner...
echo Exchanges: Binance, Bybit, KuCoin, Gate.io
echo Opening http://localhost:8000 in your browser...
echo.

set DATA_SOURCE=live

:: Open browser automatically after 2 seconds in background
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://localhost:8000"

:: Start Uvicorn web server and live scanner pipeline
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
