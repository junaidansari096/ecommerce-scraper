---
name: catalog-enrichment
description: Guide for Amazon data enrichment, multi-image scraping, approximate price estimation benchmarks, and Shopify 61-column master catalog export.
---

# Catalog Enrichment & Extraction Skill

## Workflow

1. **Spreadsheet Upload / URL Import:**
   - Supports CSV or Excel files with product names or URLs.
   - If missing columns (`Tags`, `Vendor`, `Price`, `Images`, `Description`) are selected, the engine auto-injects them into the database schema.

2. **Extraction Engine:**
   - Multi-threaded worker pool (1 to 8 concurrent threads).
   - Residential headers + Chrome Headless anti-bot profile.
   - Pre-indexed 5,596 product cache for instant zero-latency lookups.
   - Smart Approximate Price Fallback Engine: Calculates realistic retail prices from weight and category benchmarks when marketplace price is unavailable.

3. **Shopify 61-Column Export:**
   - Converts to official `product_template.csv` standard.
   - Secondary images exported as individual 3-key rows (`URL handle`, `Product image URL`, `Image position`).
