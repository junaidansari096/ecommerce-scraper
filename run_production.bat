@echo off
title StoreHub - PRODUCTION MODE [Port 8000]
echo ========================================================
echo  🚀 STOREHUB PLATFORM - PRODUCTION ENVIRONMENT
echo ========================================================
echo  Environment: PRODUCTION
echo  Port:        8000
echo  Database:    auth.db (Production Vaults)
echo  Storage:     user_data/
echo  Web URL:     http://localhost:8000
echo ========================================================
echo.
cd /d "%~dp0"

set APP_ENV=production
set PORT=8000

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    pause
    exit /b 1
)

echo Launching Production Server on http://localhost:8000 ...
python -m uvicorn server:app --host 0.0.0.0 --port 8000
pause
