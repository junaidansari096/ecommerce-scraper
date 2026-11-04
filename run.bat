@echo off
title StoreHub - E-Commerce Scraper Platform
echo ========================================================
echo Starting StoreHub E-Commerce Scraping & Shopify Platform
echo ========================================================
echo.
cd /d "%~dp0"

echo [1/2] Checking Python environment...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    pause
    exit /b 1
)

echo [2/2] Launching FastApi Uvicorn Server...
echo Web UI will be available at: http://localhost:8000
echo.
python -m uvicorn server:app --host 0.0.0.0 --port 8000 --reload
pause
