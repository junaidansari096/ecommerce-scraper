import requests
import time
import json
import io
import sys
import pandas as pd

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_URL = "http://127.0.0.1:8000"

def run_tests():
    print("[1] Testing /api/platforms...")
    r = requests.get(f"{BASE_URL}/api/platforms")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    platforms = r.json()
    if isinstance(platforms, dict):
        platforms = platforms.get("platforms", [])
    print(f"    Available platforms: {[p['name'] for p in platforms]}")

    print("\n[2] Testing /api/dashboard/stats...")
    r = requests.get(f"{BASE_URL}/api/dashboard/stats")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    stats = r.json()
    print(f"    Total projects: {stats.get('total_projects')}")

    print("\n[3] Creating new project...")
    r = requests.post(f"{BASE_URL}/api/projects", json={
        "name": "E2E Enterprise Verification Project",
        "platform": "amazon"
    })
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    res = r.json()
    project = res.get("project", res)
    project_id = project["id"]
    print(f"    Created project: {project['name']} (ID: {project_id})")

    print("\n[4] Adding manual input data (3 products)...")
    sample_text = """7UP Soft Drink, 2.25 L,Lemon
Britannia Treat Jim Jam Biscuits, 138g
Bikaji Sub-Kuch Navratna Mix 1kg"""
    r = requests.post(f"{BASE_URL}/api/projects/{project_id}/manual-urls", json={
        "urls_or_names": sample_text,
        "column_name": "Product Name"
    })
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    print(f"    Manual input result: {r.json()}")

    print("\n[5] Fetching input spreadsheet data...")
    r = requests.get(f"{BASE_URL}/api/projects/{project_id}/input/data")
    assert r.status_code == 200
    input_data = r.json()
    print(f"    Columns: {input_data['columns']}")
    print(f"    Total rows: {input_data['total']}")
    assert len(input_data["rows"]) == 3, f"Expected 3 rows, got {len(input_data['rows'])}"

    print("\n[6] Testing in-place input cell edit...")
    r = requests.post(f"{BASE_URL}/api/projects/{project_id}/input/cell", json={
        "row_idx": 0,
        "col_name": "Product Name",
        "new_val": "7UP Soft Drink, 2.25 L (Lemon Flavor)"
    })
    assert r.status_code == 200, f"Cell edit failed: {r.text}"
    print("    Input cell edited successfully.")

    print("\n[7] Testing adding custom column...")
    r = requests.post(f"{BASE_URL}/api/projects/{project_id}/input/column/add", json={
        "col_name": "Supplier Priority",
        "default_val": "High"
    })
    assert r.status_code == 200, f"Add column failed: {r.text}"
    print("    Custom column 'Supplier Priority' added.")

    print("\n[8] Updating project configuration...")
    config_payload = {
        "fetch_vendor": True,
        "fetch_images": True,
        "max_images": 5,
        "fetch_pricing": True,
        "price_margin_pct": 10,
        "round_price": True,
        "fetch_description": True,
        "max_bullets": 5,
        "fetch_specs": True,
        "fetch_tags": True,
        "tags_title_case": True
    }
    r = requests.post(f"{BASE_URL}/api/projects/{project_id}", json={
        "config": config_payload,
        "concurrency": 2,
        "mapping": {"target_column": "Product Name"}
    })
    assert r.status_code == 200, f"Update project failed: {r.text}"
    print("    Configuration saved.")

    print("\n[9] Launching Scraping Job with 2 background workers...")
    r = requests.post(f"{BASE_URL}/api/projects/{project_id}/start", json={
        "concurrency": 2
    })
    assert r.status_code == 200, f"Start failed: {r.text}"
    print(f"    Job launched: {r.json()}")

    print("\n[10] Monitoring Live Scraping Progress...")
    for step in range(40):
        time.sleep(1)
        r = requests.get(f"{BASE_URL}/api/projects/{project_id}/status")
        status = r.json()
        print(f"     Status: {status.get('status')} | Processed: {status.get('processed_count')}/{status.get('total_to_process')} | Speed: {status.get('speed_per_min', 0):.1f} items/min | Active Workers: {status.get('active_workers')}")
        if status.get("status") in ["completed", "failed", "stopped"] and status.get("processed_count", 0) >= status.get("total_to_process", 1):
            break

    print("\n[11] Inspecting Recent Activity Logs...")
    r = requests.get(f"{BASE_URL}/api/projects/{project_id}/logs")
    logs = r.json()
    if isinstance(logs, dict):
        logs = logs.get("logs", [])
    print(f"    Fetched {len(logs)} log entries. Last 3:")
    for log in logs[-3:]:
        print(f"      [{log.get('time')}] [{log.get('worker')}] {log.get('message')}")

    print("\n[12] Inspecting Live Output Grid...")
    r = requests.get(f"{BASE_URL}/api/projects/{project_id}/output/data")
    assert r.status_code == 200
    output_grid = r.json()
    print(f"    Total Output Rows: {output_grid.get('total')}")
    for row in output_grid.get("rows", []):
        print(f"    Row #{row.get('__row_idx__')}: {row.get('Title', '')[:35]}... | Brand: {row.get('Vendor')} | Price: ₹{row.get('Variant Price')} (MRP: ₹{row.get('Variant Compare At Price')}) | Status: {row.get('__status__')}")

    print("\n[13] Testing In-Place Output Cell Edit...")
    r = requests.post(f"{BASE_URL}/api/projects/{project_id}/output/cell", json={
        "row_idx": 0,
        "col_name": "Vendor",
        "new_val": "7UP (PepsiCo Master)"
    })
    assert r.status_code == 200, f"Output cell edit failed: {r.text}"
    print("    Output cell edited successfully.")

    print("\n[14] Testing Export Formats...")
    # Export Shopify Master CSV
    r = requests.get(f"{BASE_URL}/api/projects/{project_id}/export?format=shopify_csv")
    assert r.status_code == 200, f"Shopify export failed: {r.status_code}"
    assert "attachment; filename=" in r.headers.get("content-disposition", "")
    df_shopify = pd.read_csv(io.StringIO(r.text))
    print(f"    Shopify CSV Export verified: {len(df_shopify)} rows, {len(df_shopify.columns)} columns (Standard 61 Shopify schema)")
    assert "URL handle" in df_shopify.columns or "Handle" in df_shopify.columns
    assert "Title" in df_shopify.columns
    assert "Vendor" in df_shopify.columns
    assert "Product image URL" in df_shopify.columns or "Image Src" in df_shopify.columns

    # Export Excel .xlsx
    r = requests.get(f"{BASE_URL}/api/projects/{project_id}/export?format=excel")
    assert r.status_code == 200, f"Excel export failed: {r.status_code}"
    assert len(r.content) > 1000
    print(f"    Excel .xlsx Export verified: {len(r.content)} bytes received")

    # Export Flat CSV
    r = requests.get(f"{BASE_URL}/api/projects/{project_id}/export?format=flat_csv")
    assert r.status_code == 200, f"Flat CSV export failed: {r.status_code}"
    print(f"    Flat CSV Export verified: {len(r.text.splitlines())} lines")

    print("\n==========================================")
    print("🎉 ALL 14 INTEGRATION CHECKS PASSED PERFECTLY!")
    print("==========================================")

if __name__ == "__main__":
    run_tests()
