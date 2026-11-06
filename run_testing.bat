@echo off
title StoreHub - TESTING SANDBOX [Port 8001]
echo ========================================================
echo  🧪 STOREHUB PLATFORM - TESTING / STAGING SANDBOX
echo ========================================================
echo  Environment: TESTING / STAGING
echo  Port:        8001
echo  Database:    auth_test.db (Isolated Test Database)
echo  Storage:     user_data_test/ (Test Vaults)
echo  Web URL:     http://localhost:8001
echo ========================================================
echo  Note: Production customer data and vaults are UNTOUCHED.
echo ========================================================
echo.
cd /d "%~dp0"

set APP_ENV=testing
set PORT=8001

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    pause
    exit /b 1
)

echo Launching Testing Sandbox Server on http://localhost:8001 ...
python -m uvicorn server:app --host 0.0.0.0 --port 8001 --reload
pause
