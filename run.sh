#!/usr/bin/env bash
echo "========================================================"
echo "Starting StoreHub E-Commerce Scraping & Shopify Platform"
echo "========================================================"
cd "$(dirname "$0")"

if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python 3 is not installed or not in PATH!"
    exit 1
fi

echo "Launching FastAPI Uvicorn Server..."
echo "Web UI will be available at: http://localhost:8000"
echo ""
python3 -m uvicorn server:app --host 0.0.0.0 --port 8000 --reload
