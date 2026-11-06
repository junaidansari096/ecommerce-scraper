---
name: local-testing
description: Step-by-step procedures to test the StoreHub E-Commerce Scraper Platform locally across Production (Port 8000) and Testing Sandbox (Port 8001), validating customer isolation, Shopify 61-column compliance, and scraper worker concurrency.
---

# StoreHub Local Testing Skill

This skill guides automated and manual verification of the dual-environment architecture.

## 1. Environment Verification

### Testing Sandbox (`APP_ENV=testing`)
- **Port:** 8001
- **Database:** `auth_test.db`
- **Customer Storage:** `user_data_test/{user_id}/`
- **UI Indicator:** `🧪 Testing Sandbox` amber badge

### Production Mode (`APP_ENV=production`)
- **Port:** 8000
- **Database:** `auth.db`
- **Customer Storage:** `user_data/{user_id}/`
- **UI Indicator:** `🟢 Production` emerald badge

## 2. Automated Test Execution

Run the complete test suite with `pytest`:
```bash
pytest tests/
```

Individual component tests:
```bash
# Environment & Vault isolation
python tests/test_auth_isolation.py

# Shopify 61-column schema & multi-row secondary images
python tests/test_shopify_features.py

# Multi-image gallery (up to 20 images) & price fallback reasoning
python tests/test_images_and_pricing.py
```

## 3. Launching Local Services

- Launch Testing: `run_testing.bat` (Windows) or `./run_testing.sh` (Linux/Mac)
- Launch Production: `run_production.bat` (Windows) or `./run_production.sh` (Linux/Mac)
