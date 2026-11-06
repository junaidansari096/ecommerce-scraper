#!/usr/bin/env bash
echo "========================================================"
echo " 🚀 STOREHUB PLATFORM - PRODUCTION ENVIRONMENT"
echo "========================================================"
export APP_ENV=production
export PORT=8000
python3 -m uvicorn server:app --host 0.0.0.0 --port 8000
