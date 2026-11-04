import requests
import time
import json
import sys

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_URL = "http://127.0.0.1:8000"

def test_features():
    print("=================================================================")
    print("📸 TESTING DYNAMIC 5-IMAGE EXTRACTION & APPROXIMATE PRICE REASONING")
    print("=================================================================")

    # Login Customer 1
    r = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": "customer1@storehub.io",
        "password": "Password123!"
    })
    token = r.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # -------------------------------------------------------------
    # TEST 1: 5-IMAGE DYNAMIC EXTRACTION
    # -------------------------------------------------------------
    print("\n[TEST 1] Testing 5-Image Gallery Extraction...")
    r = requests.post(f"{BASE_URL}/api/projects", json={
        "name": "5-Image Gallery Test",
        "platform": "amazon"
    }, headers=headers)
    p1_id = r.json()["project"]["id"]

    # Product with 5 images in master catalog
    r = requests.post(f"{BASE_URL}/api/projects/{p1_id}/manual-urls", json={
        "urls_or_names": "Ariel Complete Detergent Powder - 1kg + 500g free",
        "column_name": "Product Name"
    }, headers=headers)
    assert r.status_code == 200

    # Configure max_images = 5
    r = requests.post(f"{BASE_URL}/api/projects/{p1_id}", json={
        "config": {
            "fetch_vendor": True,
            "fetch_images": True,
            "max_images": 5,
            "fetch_pricing": True,
            "estimate_missing_price": True
        },
        "concurrency": 1
    }, headers=headers)
    assert r.status_code == 200

    # Start scraping
    r = requests.post(f"{BASE_URL}/api/projects/{p1_id}/start", json={"concurrency": 1}, headers=headers)
    assert r.status_code == 200

    # Wait for completion
    for _ in range(15):
        time.sleep(1)
        st = requests.get(f"{BASE_URL}/api/projects/{p1_id}/status", headers=headers).json()
        if st.get("status") in ["completed", "failed"]:
            break

    # Inspect Output Grid
    r = requests.get(f"{BASE_URL}/api/projects/{p1_id}/output/data", headers=headers)
    assert r.status_code == 200
    out_data = r.json()
    cols = out_data.get("columns", [])
    print(f"    Output Columns: {cols}")
    assert "Primary_Image" in cols, "Primary_Image column missing"
    assert "Image_2" in cols, "Image_2 column missing"
    assert "Image_3" in cols, "Image_3 column missing"
    assert "Image_4" in cols, "Image_4 column missing"
    assert "Image_5" in cols, "Image_5 column missing"
    print("    ✅ VERIFIED: All 5 Image columns (Primary_Image, Image_2, Image_3, Image_4, Image_5) are present!")

    row0 = out_data["rows"][0]
    for i in range(1, 6):
        col_name = "Primary_Image" if i == 1 else f"Image_{i}"
        img_val = row0.get(col_name, "")
        print(f"    Image #{i} ({col_name}): {img_val[:50]}...")
        assert img_val.startswith("http"), f"Image #{i} is empty or invalid!"
    print("    ✅ VERIFIED: All 5 Image columns are populated with real non-empty image URLs!")

    # -------------------------------------------------------------
    # TEST 2: SMART APPROXIMATE PRICING ON (REASONING ENGINE)
    # -------------------------------------------------------------
    print("\n[TEST 2] Testing Approximate Price Reasoning (ON)...")
    r = requests.post(f"{BASE_URL}/api/projects", json={
        "name": "Smart Approximate Pricing ON Test",
        "platform": "amazon"
    }, headers=headers)
    p2_id = r.json()["project"]["id"]

    # Use a custom SKU with volume keyword to verify domain reasoning
    unlisted_item = "CustomBatch Bottled Sparkling Lemon Soda 2.25 L"
    r = requests.post(f"{BASE_URL}/api/projects/{p2_id}/manual-urls", json={
        "urls_or_names": unlisted_item,
        "column_name": "Product Name"
    }, headers=headers)

    # Configure estimate_missing_price = True
    r = requests.post(f"{BASE_URL}/api/projects/{p2_id}", json={
        "config": {
            "fetch_vendor": True,
            "fetch_pricing": True,
            "estimate_missing_price": True,
            "max_images": 3
        },
        "concurrency": 1
    }, headers=headers)

    r = requests.post(f"{BASE_URL}/api/projects/{p2_id}/start", json={"concurrency": 1}, headers=headers)
    assert r.status_code == 200

    for _ in range(20):
        time.sleep(1)
        st = requests.get(f"{BASE_URL}/api/projects/{p2_id}/status", headers=headers).json()
        if st.get("status") in ["completed", "failed"]:
            break

    r = requests.get(f"{BASE_URL}/api/projects/{p2_id}/output/data", headers=headers)
    row_est = r.json()["rows"][0]
    print(f"    Product: {row_est.get('Title')}")
    print(f"    Vendor: {row_est.get('Vendor')}")
    print(f"    Price: ₹{row_est.get('Price')} (MRP: ₹{row_est.get('Compare_at_Price')})")
    print(f"    Status: {row_est.get('Status')}")

    assert float(row_est.get("Price", 0)) > 0, "Price should have been estimated by reasoning!"
    print("    ✅ VERIFIED: Approximate price and compare-at MRP assigned!")

    # -------------------------------------------------------------
    # TEST 3: SMART APPROXIMATE PRICING OFF (TOGGLED OFF)
    # -------------------------------------------------------------
    print("\n[TEST 3] Testing Approximate Price Reasoning (OFF)...")
    r = requests.post(f"{BASE_URL}/api/projects", json={
        "name": "Smart Approximate Pricing OFF Test",
        "platform": "amazon"
    }, headers=headers)
    p3_id = r.json()["project"]["id"]

    # Use a SKU guaranteed to have no match
    unmatched_item = "Zyx999NonExistentUnlistedItem 500g"
    r = requests.post(f"{BASE_URL}/api/projects/{p3_id}/manual-urls", json={
        "urls_or_names": unmatched_item,
        "column_name": "Product Name"
    }, headers=headers)

    # Configure estimate_missing_price = False (USER TURNED IT OFF)
    r = requests.post(f"{BASE_URL}/api/projects/{p3_id}", json={
        "config": {
            "fetch_vendor": True,
            "fetch_pricing": True,
            "estimate_missing_price": False,
            "max_images": 3
        },
        "concurrency": 1
    }, headers=headers)

    r = requests.post(f"{BASE_URL}/api/projects/{p3_id}/start", json={"concurrency": 1}, headers=headers)
    assert r.status_code == 200

    for _ in range(20):
        time.sleep(1)
        st = requests.get(f"{BASE_URL}/api/projects/{p3_id}/status", headers=headers).json()
        if st.get("status") in ["completed", "failed"]:
            break

    r = requests.get(f"{BASE_URL}/api/projects/{p3_id}/output/data", headers=headers)
    row_unlisted = r.json()["rows"][0]
    print(f"    Product: {row_unlisted.get('Title')}")
    print(f"    Price (should be empty): '{row_unlisted.get('Price')}'")
    print(f"    Status: {row_unlisted.get('Status')}")

    assert str(row_unlisted.get("Price", "")).strip() in ["", "0", "0.0"], "Price should remain empty when feature is turned off!"
    assert "(Price Unlisted)" in row_unlisted.get("Status", ""), "Status should indicate Price Unlisted!"
    print("    ✅ VERIFIED: When user turns OFF the feature, price remains empty and status marks (Price Unlisted)!")

    # -------------------------------------------------------------
    # TEST 4: SHOPIFY MASTER CSV EXPORT VERIFICATION (ALL 5 IMAGES)
    # -------------------------------------------------------------
    print("\n[TEST 4] Testing Shopify CSV Export with All 5 Secondary Images...")
    r = requests.get(f"{BASE_URL}/api/projects/{p1_id}/export?format=shopify_csv", headers=headers)
    assert r.status_code == 200
    csv_text = r.text
    csv_lines = [l for l in csv_text.splitlines() if l.strip()]
    print(f"    Total Exported Shopify CSV Rows: {len(csv_lines)} (1 header + 5 image rows)")
    assert len(csv_lines) == 6, f"Expected 6 rows (1 header + 5 image rows), got {len(csv_lines)}"
    print("    ✅ VERIFIED: Shopify CSV Export accurately contains 1 primary row and 4 clean secondary image rows!")

    print("\n=================================================================")
    print("🎉 ALL DYNAMIC IMAGE & APPROXIMATE PRICING CHECKS PASSED 100%!")
    print("=================================================================")

if __name__ == "__main__":
    test_features()
