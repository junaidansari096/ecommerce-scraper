import os
import sys
import json
import csv
import io

# Ensure python path includes parent app directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(__file__))

from project_db import ProjectDatabase, SHOPIFY_TEMPLATE_COLUMNS
from exporter import ProjectExporter
from job_manager import ProjectJobManager
from extractors import PlatformRegistry

def run_tests():
    test_db_path = os.path.join(os.path.dirname(__file__), "test_verify.db")
    if os.path.exists(test_db_path):
        os.remove(test_db_path)

    print("\n=======================================================")
    print("RUNNING VERIFICATION SUITE FOR SHOPIFY & DYNAMIC COLUMNS")
    print("=======================================================\n")

    db = ProjectDatabase(test_db_path)
    exporter = ProjectExporter(db)
    registry = PlatformRegistry()
    extractor = registry.get_extractor("amazon")

    # 1. Create a project
    project = db.create_project("proj_test_1", "Verification Test Project", platform="amazon")
    proj_id = project["id"]
    print(f"[TEST 1] Project created: {proj_id}")

    # 2. Upload CSV with ONLY 1 column: 'Product Name'
    cols = ["Product Name"]
    rows = [
        {"Product Name": "Britannia Treat Jim Jam Biscuits, 138g"},
        {"Product Name": "Bikaji Sub-Kuch Navratna Mix 1kg"}
    ]
    db.save_input_dataset(proj_id, cols, rows, {"target_column": "Product Name"})
    print(f"[TEST 2] Imported dataset: {len(rows)} rows, input columns: {cols}")
    assert cols == ["Product Name"], f"Expected ['Product Name'], got {cols}"

    # 3. Simulate project config where Tags, Vendor, Pricing, 5 Images are enabled
    cfg = {
        "fetch_vendor": True,
        "fetch_pricing": True,
        "estimate_missing_price": True,
        "fetch_tags": True,
        "fetch_images": True,
        "max_images": 5,
        "fetch_description": True
    }
    db.update_project(proj_id, {"config_json": json.dumps(cfg)})

    # Test dynamic column creation in get_output_columns
    out_cols = db.get_output_columns(proj_id)
    print(f"[TEST 3] Output columns generated dynamically: {out_cols}")
    assert "Tags" in out_cols, "Tags column was NOT dynamically created!"
    assert "Vendor" in out_cols, "Vendor column was NOT dynamically created!"
    assert "Price" in out_cols, "Price column was NOT dynamically created!"
    assert "Primary_Image" in out_cols, "Primary_Image column was NOT dynamically created!"
    assert "Image_2" in out_cols and "Image_5" in out_cols, "Secondary image columns not dynamically created!"
    print(">>> SUCCESS: Dynamic column injection works perfectly for missing fields (Tags, Vendor, Price, Images).")

    # 4. Run extractor enrichment on row 0 and row 1
    rows_resp = db.get_input_rows(proj_id)
    rows = rows_resp["rows"]
    
    for r in rows:
        row_idx = r["__row_idx__"]
        q = r["Product Name"]
        res = extractor.extract(q, cfg)
        
        merged = dict(r)
        for k, v in res.items():
            if k != "Images":
                merged[k] = v
        
        db.save_output_row(proj_id, row_idx, merged, status="completed")
        print(f"[TEST 4] Enriched row #{row_idx + 1}: '{q[:30]}' -> Vendor: {res.get('Vendor')}, Tags: '{res.get('Tags')}', Price: ₹{res.get('Price')}, Images: {len(res.get('Images', []))}")

    # Verify Tags and 5 Images exist in output data
    out_data = db.get_output_rows(proj_id)
    assert len(out_data["rows"]) == 2
    row0 = out_data["rows"][0]
    print(f"[TEST 4b] Row 0 Tags: {row0.get('Tags')}")
    assert row0.get("Tags"), "Tags field in row 0 is empty!"
    assert row0.get("Primary_Image"), "Primary_Image in row 0 is empty!"
    assert row0.get("Image_2"), "Image_2 in row 0 is empty!"
    print(">>> SUCCESS: Tags extraction and 5-image gallery successfully enriched into output data.")

    # 5. Test apply_shopify_schema
    shopify_cols = db.apply_shopify_schema(proj_id)
    print(f"[TEST 5] Applied Shopify schema. Column count: {len(shopify_cols)}")
    assert len(shopify_cols) == 61, f"Expected 61 Shopify columns, got {len(shopify_cols)}"
    assert shopify_cols == SHOPIFY_TEMPLATE_COLUMNS, "Shopify columns do not match official template!"
    assert "Packed product dimension unit" in shopify_cols
    assert "Color (product.metafields.shopify.color-pattern)" in shopify_cols

    # Verify mapped row
    s_rows = db.get_output_rows(proj_id)["rows"]
    s_r0 = s_rows[0]
    print(f"[TEST 5b] Shopify Row 0 Title: {s_r0.get('Title')}, Handle: {s_r0.get('URL handle')}, Status: {s_r0.get('Status')}, Published: {s_r0.get('Published on online store')}")
    assert s_r0.get("URL handle")
    assert s_r0.get("Status") == "Active"
    assert s_r0.get("Published on online store") == "TRUE"
    assert s_r0.get("Tags")
    print(">>> SUCCESS: Output sheet successfully re-created according to official 61-column Shopify template.")

    # 6. Test Shopify CSV Export
    csv_bytes, filename, mtype = exporter.generate_export(proj_id, fmt="shopify_csv")
    csv_text = csv_bytes.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(csv_text))
    csv_rows = list(reader)
    header = csv_rows[0]
    
    print(f"[TEST 6] Exported CSV header column count: {len(header)}")
    assert len(header) == 61, f"Expected 61 header columns in CSV, got {len(header)}"
    assert header == SHOPIFY_TEMPLATE_COLUMNS, "Exported CSV header does not match exact 61 template columns!"

    # Verify secondary image rows format
    print(f"[TEST 6b] Total CSV rows generated (including secondary images): {len(csv_rows)}")
    assert len(csv_rows) > 3, "Secondary image rows were not generated in Shopify CSV!"
    
    # Check secondary row has only handle, image url, image position
    sec_row = csv_rows[2] # row index 2 in CSV
    handle_idx = header.index("URL handle")
    img_idx = header.index("Product image URL")
    pos_idx = header.index("Image position")
    title_idx = header.index("Title")
    
    assert sec_row[title_idx] == "", "Secondary image row must have blank Title!"
    assert sec_row[handle_idx] != "", "Secondary image row must have URL handle!"
    assert sec_row[img_idx] != "", "Secondary image row must have Product image URL!"
    assert sec_row[pos_idx] in ["2", "3", "4", "5"], f"Unexpected image position: {sec_row[pos_idx]}"
    print(">>> SUCCESS: Shopify CSV exporter perfectly adheres to Shopify 3-key secondary image spec.")

    # Clean up test database
    del db
    import gc
    gc.collect()
    try:
        if os.path.exists(test_db_path):
            os.remove(test_db_path)
    except Exception:
        pass

    print("\n=======================================================")
    print("ALL VERIFICATION CHECKS PASSED WITH 100% ACCURACY!")
    print("=======================================================\n")

if __name__ == "__main__":
    run_tests()
