#!/usr/bin/env bash
echo "========================================================"
echo " 🧪 STOREHUB PLATFORM - TESTING / STAGING SANDBOX"
echo "========================================================"
export APP_ENV=testing
export PORT=8001
python3 -m uvicorn server:app --host 0.0.0.0 --port 8001 --reload
