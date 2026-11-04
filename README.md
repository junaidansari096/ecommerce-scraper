# StoreHub — Professional E-Commerce Scraping & Shopify Catalog Platform

A full-stack, enterprise-grade e-commerce data extraction and catalog management platform designed with a clean Linear/Stripe aesthetic. Customers can upload product lists (CSV/XLSX or single-column names), configure custom scraping rules, run concurrent multi-threaded extraction workers, live-stream and edit results in an interactive spreadsheet, and export production-ready datasets formatted for Shopify or formatted Excel workbooks.

---

## 🌟 Key Features

### 1. Zero-Friction Data Import & Live Spreadsheet Editor
- Upload any CSV or Excel spreadsheet (even single-column files with only `Product Name`).
- Review and edit raw records in an interactive web spreadsheet prior to running scraping jobs.
- Direct row editing, pagination, keyword searching, and status filtering.

### 2. Dynamic Missing Field Injection
- If the uploaded sheet lacks fields you want to extract (`Tags`, `Vendor`, `Price`, `Images`, `Description`), the platform **automatically detects and injects those columns** into the database and spreadsheet view.

### 3. Highly Tunable Extraction Rules
- **Multi-Image Extraction:** Configure min/max gallery images per product (1 to 20 images).
- **Price Extraction & Reasoning Engine:** Extract live price and compare-at MRP. If a product's price is unlisted or missing on Amazon, toggle the **Smart Approximate Price Fallback Engine**, which uses product weight, volume, and category benchmarks to reason a realistic retail price.
- **Title Case Category Tags:** Automatically classifies items into standardized Title Case tags.
- **Clean Descriptions & Bullets:** Generates sanitized HTML descriptions with bullet points while stripping marketplace noise.

### 4. Official 61-Column Shopify Template Compliance
- 1-Click format conversion into the official **61-column Shopify Product CSV template** (`product_template.csv`).
- Automatically populates Shopify standards (`Published: TRUE`, `Status: Active`, `Requires Shipping: TRUE`, `Charge Tax: TRUE`, `Fulfillment Service: manual`, `Inventory Tracker: shopify`, `Default Title`, etc.).
- **Multi-Row Secondary Image Architecture:** Accurately structures secondary gallery images into separate rows with strictly 3 keys (`URL handle`, `Product image URL`, `Image position`), fully compliant with Shopify Admin importer specifications.

### 5. Multi-Tenant Customer Authentication & Data Isolation
- Stateless **JWT (HMAC-SHA256)** authentication with salted **Bcrypt** password hashing.
- **Physical SQLite Isolation:** Each customer's databases (`projects.db`, outputs, logs) are physically segregated under `user_data/{user_id}/`. Customer A can never read or query Customer B's records.

### 6. Multi-Worker Concurrency & Live Monitoring
- Configure background scraping workers (1 to 8 concurrent threads).
- Real-time event log streaming, speed calculation (items/min), ETA counter, and job controls (Pause, Resume, Stop, Retry Failed).

---

## 📁 Repository Structure

```
ecommerce-scraper-app/
├── server.py              # FastAPI server & REST API endpoints
├── project_db.py          # SQLite database layer, schema manager & migrations
├── job_manager.py         # Multi-worker thread orchestrator & job state
├── scraper_engine.py      # Amazon extraction engine, cache lookup & reasoning
├── extractors.py          # Platform adapters & standardized result builders
├── exporter.py            # Shopify 61-col CSV, Formatted Excel (.xlsx), Flat CSV
├── auth_manager.py        # Multi-tenant JWT + Bcrypt auth & physical isolation
├── catalog_manager.py     # Master catalog indexing & search
├── spreadsheet_manager.py # In-memory spreadsheet parsing & edits
├── batch_machine.py       # High-throughput batch processing engine
├── automation_runner.py   # Scheduled extraction engine
├── templates/
│   └── index.html         # Premium Linear/Stripe-inspired single-page UI
├── data/
│   ├── all_products_master_enriched.csv  # Pre-indexed master verified catalog
│   └── amazon_cache_v3.json              # Instant-lookup Amazon cache
├── tests/
│   ├── test_shopify_features.py          # Comprehensive Shopify & dynamic column tests
│   ├── test_auth_isolation.py            # Physical customer isolation verification
│   ├── test_images_and_pricing.py        # 5-image gallery & price reasoning tests
│   └── test_e2e.py                       # Full end-to-end integration test
├── .gitignore
├── requirements.txt
├── run.bat                # 1-Click Windows launcher
└── run.sh                 # 1-Click Linux/macOS launcher
```

---

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.9 or higher
- Git (optional, for version control)
- Google Chrome (optional, used for headless stealth scraping fallback)

### 2. Installation

Clone or copy this folder and install dependencies:

```bash
cd ecommerce-scraper-app
pip install -r requirements.txt
```

### 3. Launching the Platform

**On Windows:**
Double-click `run.bat` or run:
```bash
python -m uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

**On Linux / macOS:**
```bash
chmod +x run.sh
./run.sh
```

Open your browser and navigate to:
**`http://localhost:8000`**

---

## 🧪 Running the Verification Test Suite

Run the automated test suite to verify all core components:

```bash
# Shopify 61-column compliance & dynamic column creation
python tests/test_shopify_features.py

# Multi-tenant customer security & physical isolation
python tests/test_auth_isolation.py

# Multi-image extraction & pricing reasoning engine
python tests/test_images_and_pricing.py
```

---

## 📄 License
MIT License. Built for high-volume enterprise e-commerce operations.
