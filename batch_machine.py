import os
import sys
import io
import re
import csv
import json
import time
import threading
import pandas as pd
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Optional

if sys.stdout:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from scraper_engine import AmazonScraperEngine

class BatchEnrichmentMachine:
    def __init__(self):
        self.scraper = AmazonScraperEngine()
        self.lock = threading.RLock()
        
        # Uploaded source data
        self.source_filename: str = ""
        self.source_columns: List[str] = []
        self.source_rows: List[Dict[str, Any]] = []
        
        # Machine Execution State
        self.is_running: bool = False
        self.stop_requested: bool = False
        self.progress: int = 0
        self.total_to_process: int = 0
        self.processed_count: int = 0
        self.start_index: int = 0
        self.end_index: int = 0
        self.start_time: float = 0.0
        self.status_message: str = "Ready for input"
        
        # Live spontaneous output results
        self.live_output: List[Dict[str, Any]] = []
        self.logs: List[Dict[str, str]] = []
        
        # Generated export artifacts
        self.final_csv_bytes: Optional[bytes] = None
        self.final_excel_bytes: Optional[bytes] = None
        self.output_format: str = "shopify_csv"

    def log(self, text: str, level: str = "info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = {"time": timestamp, "text": text, "level": level}
        with self.lock:
            self.logs.append(entry)
            if len(self.logs) > 1000:
                self.logs.pop(0)
        try:
            print(f"[{timestamp}] [MACHINE] {text}")
        except Exception:
            try:
                safe_text = text.encode("ascii", "replace").decode("ascii")
                print(f"[{timestamp}] [MACHINE] {safe_text}")
            except Exception:
                pass

    def load_uploaded_file(self, file_bytes: bytes, filename: str) -> Dict[str, Any]:
        """Load any uploaded CSV or Excel file."""
        with self.lock:
            self.source_filename = filename
            ext = os.path.splitext(filename)[1].lower()

            if ext in [".xlsx", ".xls"]:
                df = pd.read_excel(io.BytesIO(file_bytes), dtype=str)
            else:
                try:
                    df = pd.read_csv(io.BytesIO(file_bytes), dtype=str, encoding="utf-8")
                except Exception:
                    df = pd.read_csv(io.BytesIO(file_bytes), dtype=str, encoding="latin-1")

            df = df.fillna("")
            self.source_columns = list(df.columns)
            self.source_rows = df.to_dict(orient="records")

            # Try to auto-detect product name column
            detected_col = ""
            for c in self.source_columns:
                c_low = c.lower()
                if any(k in c_low for k in ["title", "product", "name", "item", "description"]):
                    detected_col = c
                    break
            if not detected_col and self.source_columns:
                detected_col = self.source_columns[0]

            self.status_message = f"Uploaded {filename}: {len(self.source_rows)} products loaded."
            self.log(f"File uploaded: {filename} with {len(self.source_rows)} rows.", "success")

            return {
                "success": True,
                "filename": filename,
                "total_rows": len(self.source_rows),
                "columns": self.source_columns,
                "detected_column": detected_col,
                "sample_rows": self.source_rows[:5]
            }

    def start_machine(self, config: Dict[str, Any]):
        with self.lock:
            if self.is_running:
                return False, "Machine is already running!"
            if not self.source_rows:
                return False, "Please upload a CSV or Excel file first!"

            target_col = config.get("target_column", "")
            if not target_col or target_col not in self.source_columns:
                return False, f"Target product name column '{target_col}' not found in file."

            total_avail = len(self.source_rows)
            start_idx = max(0, int(config.get("start_index", 0)))
            end_idx = min(total_avail, int(config.get("end_index", total_avail)))
            if start_idx >= end_idx:
                return False, f"Invalid range: {start_idx} to {end_idx} (Total available: {total_avail})"

            self.is_running = True
            self.stop_requested = False
            self.start_index = start_idx
            self.end_index = end_idx
            self.total_to_process = end_idx - start_idx
            self.processed_count = 0
            self.progress = 0
            self.start_time = time.time()
            self.status_message = f"Enriching products {start_idx} to {end_idx}..."
            self.live_output = []
            self.logs = []
            self.final_csv_bytes = None
            self.final_excel_bytes = None
            self.output_format = config.get("output_format", "shopify_csv")

        # Run background thread
        thread = threading.Thread(
            target=self._run_machine_worker,
            args=(target_col, config),
            daemon=True
        )
        thread.start()
        return True, "Enrichment machine started successfully"

    def stop_machine(self):
        with self.lock:
            if self.is_running:
                self.stop_requested = True
                self.log("Stop requested by user...", "warn")
                return True, "Cancellation requested"
            return False, "Machine is not running"

    def _run_machine_worker(self, target_col: str, config: Dict[str, Any]):
        rules = config.get("rules", {})
        concurrency = int(config.get("concurrency", 2))
        rows_to_process = self.source_rows[self.start_index:self.end_index]

        self.log(f"Starting batch machine: {len(rows_to_process)} items with {concurrency} workers...")

        def process_single_item(item_index: int, row_data: Dict[str, Any]):
            if self.stop_requested:
                return None

            product_name = str(row_data.get(target_col, "")).strip()
            if not product_name:
                return {
                    "index": item_index,
                    "name": product_name,
                    "status": "Skipped (empty)",
                    "vendor": "",
                    "price": "",
                    "mrp": "",
                    "images": [],
                    "tags": [],
                    "raw": row_data
                }

            self.log(f"[{item_index+1}] Searching: '{product_name[:40]}...'")

            try:
                # 1. Tier 1: Instant Local Verified Catalog & Cache Match
                local_match = self.scraper.lookup_local(product_name)
                if local_match:
                    vendor = local_match.get("vendor", "") if rules.get("add_vendor", True) else ""
                    price = str(local_match.get("price", "")) if rules.get("add_pricing", True) else ""
                    mrp = str(local_match.get("mrp", "")) if rules.get("add_pricing", True) else ""
                    images = local_match.get("images", []) if rules.get("add_images", True) else []
                    tags = local_match.get("tags", []) if rules.get("add_tags", True) else []
                    desc = local_match.get("description", "") if rules.get("add_description", True) else ""

                    res_entry = {
                        "index": item_index,
                        "name": product_name,
                        "scraped_title": local_match.get("title") or product_name,
                        "asin": local_match.get("asin", ""),
                        "status": "Enriched (Verified) ✅",
                        "vendor": vendor,
                        "price": price,
                        "mrp": mrp,
                        "images": images,
                        "tags": tags,
                        "description": desc,
                        "raw": row_data
                    }
                    self.log(f"[{item_index+1}] ✅ Verified Match: '{vendor}', ₹{price}, Images: {len(images)}", "success")
                    return res_entry

                # 2. Tier 2: Anti-Bot Stealth Amazon Search
                search_res = self.scraper.search_amazon_india(product_name)
                if not search_res:
                    # Provide clean fallbacks based on rules
                    vendor = self.scraper.resolve_brand_name(product_name) if rules.get("add_vendor", True) else ""
                    tags = self.scraper.resolve_tags(product_name, vendor) if rules.get("add_tags", True) else []
                    return {
                        "index": item_index,
                        "name": product_name,
                        "status": "Unmatched (Brand Tagged)",
                        "vendor": vendor,
                        "price": "" if rules.get("add_pricing", True) else "",
                        "mrp": "" if rules.get("add_pricing", True) else "",
                        "images": [],
                        "tags": tags,
                        "description": f"<p>{product_name}</p>" if rules.get("add_description", True) else "",
                        "raw": row_data
                    }

                # 3. Scrape details
                details = self.scraper.scrape_product_details(search_res["url"], rules, search_fallback=search_res)
                
                result_entry = {
                    "index": item_index,
                    "name": product_name,
                    "scraped_title": details.get("title") or product_name,
                    "asin": details.get("asin", ""),
                    "status": "Enriched ✅",
                    "vendor": details.get("vendor", "") if rules.get("add_vendor", True) else "",
                    "price": str(details.get("price", "")) if rules.get("add_pricing", True) else "",
                    "mrp": str(details.get("compare_at_price", "")) if rules.get("add_pricing", True) else "",
                    "images": details.get("images", []) if rules.get("add_images", True) else [],
                    "tags": details.get("tags", []) if rules.get("add_tags", True) else [],
                    "description": details.get("description_html", "") if rules.get("add_description", True) else "",
                    "raw": row_data
                }

                self.log(f"[{item_index+1}] ✅ Enriched: Vendor: '{result_entry['vendor']}', Price: ₹{result_entry['price']}, Images: {len(result_entry['images'])}", "success")
                return result_entry

            except Exception as e:
                self.log(f"[{item_index+1}] ❌ Error: {str(e)}", "error")
                return {
                    "index": item_index,
                    "name": product_name,
                    "status": f"Error: {str(e)}",
                    "vendor": "",
                    "price": "",
                    "mrp": "",
                    "images": [],
                    "tags": [],
                    "raw": row_data
                }

        # Multi-worker processing with spontaneous real-time completion
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(process_single_item, idx, r) for idx, r in enumerate(rows_to_process, start=self.start_index)]

            for future in as_completed(futures):
                if self.stop_requested:
                    break
                try:
                    res = future.result()
                    if res:
                        with self.lock:
                            self.live_output.append(res)
                            self.processed_count += 1
                            self.progress = int((self.processed_count / max(self.total_to_process, 1)) * 100)
                except Exception as e:
                    self.log(f"Worker exception: {e}", "error")

        with self.lock:
            # Sort live output by original row index so export is perfectly ordered
            self.live_output.sort(key=lambda x: x.get("index", 0))
            self.is_running = False
            if self.stop_requested:
                self.status_message = f"Machine stopped by user at {self.processed_count}/{self.total_to_process} items."
                self.log("Batch processing stopped.", "warn")
            else:
                self.status_message = f"Completed! Processed {self.processed_count} items."
                self.log("Batch processing successfully finished!", "success")
                self.progress = 100

            # Build final export bytes
            self._generate_export_artifacts(config)

    def _generate_export_artifacts(self, config: Dict[str, Any]):
        """Build Shopify CSV or Excel file from live output."""
        if not self.live_output:
            return

        fmt = self.output_format
        
        if fmt == "shopify_csv":
            # Generate Shopify 61-column standard format
            fieldnames = [
                "Title", "URL handle", "Description", "Vendor", "Product category", "Type", "Tags",
                "Published on online store", "Status", "SKU", "Variant Barcodes", "Option1 name",
                "Option1 value", "Option1 Linked To", "Option2 name", "Option2 value", "Option2 Linked To",
                "Option3 name", "Option3 value", "Option3 Linked To", "Price", "Compare-at price",
                "Cost per item", "Charge tax", "Tax code", "Unit price total measure", "Unit price total measure unit",
                "Unit price base measure", "Unit price base measure unit", "Inventory tracker",
                "Inventory quantity", "Continue selling when out of stock", "Weight value (grams)",
                "Weight unit for display", "Requires shipping", "Fulfillment service", "Product image URL",
                "Image position", "Image alt text", "Gift card", "SEO title", "SEO description",
                "Google Shopping / Google Product Category", "Google Shopping / Gender", "Google Shopping / Age Group",
                "Google Shopping / MPN", "Google Shopping / Condition", "Google Shopping / Custom Product",
                "Google Shopping / Custom Label 0", "Google Shopping / Custom Label 1", "Google Shopping / Custom Label 2",
                "Google Shopping / Custom Label 3", "Google Shopping / Custom Label 4", "Variant image URL",
                "Variant weight unit", "Variant tax code", "Cost per item (USD)", "Included / United States",
                "Price / United States", "Compare-at price / United States", "Status / United States"
            ]

            all_rows = []
            for item in self.live_output:
                title = item.get("scraped_title") or item.get("name", "Product")
                handle = re.sub(r'[^a-zA-Z0-9]+', '-', title.lower()).strip('-')[:80]
                images = item.get("images", [])

                # Primary row
                p_row = {fn: "" for fn in fieldnames}
                p_row["Title"] = title
                p_row["URL handle"] = handle
                p_row["Description"] = item.get("description", f"<p>{title}</p>")
                p_row["Vendor"] = item.get("vendor", "Generic")
                p_row["Tags"] = ", ".join(item.get("tags", []))
                p_row["Price"] = str(item.get("price", "499"))
                p_row["Compare-at price"] = str(item.get("mrp", "699"))
                p_row["Status"] = "Active"
                p_row["Published on online store"] = "true"
                p_row["Requires shipping"] = "true"
                p_row["Charge tax"] = "true"

                if images:
                    p_row["Product image URL"] = images[0]
                    p_row["Image position"] = "1"

                all_rows.append(p_row)

                # Secondary rows (Strict Shopify 3-key format)
                for idx, img_u in enumerate(images[1:], 2):
                    sec_r = {fn: "" for fn in fieldnames}
                    sec_r["URL handle"] = handle
                    sec_r["Product image URL"] = img_u
                    sec_r["Image position"] = str(idx)
                    all_rows.append(sec_r)

            # Export to CSV
            out_buf = io.StringIO()
            writer = csv.DictWriter(out_buf, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_rows)
            self.final_csv_bytes = out_buf.getvalue().encode("utf-8-sig")

        elif fmt == "excel":
            # Generate Excel .xlsx
            flat_rows = []
            for item in self.live_output:
                imgs = item.get("images", [])
                flat_rows.append({
                    "Product Name": item.get("name", ""),
                    "Enriched Title": item.get("scraped_title", ""),
                    "Brand / Vendor": item.get("vendor", ""),
                    "Retail Price": item.get("price", ""),
                    "Compare MRP": item.get("mrp", ""),
                    "Subcategory Tags": ", ".join(item.get("tags", [])),
                    "Primary Image": imgs[0] if imgs else "",
                    "Image 2": imgs[1] if len(imgs) > 1 else "",
                    "Image 3": imgs[2] if len(imgs) > 2 else "",
                    "Image 4": imgs[3] if len(imgs) > 3 else "",
                    "Image 5": imgs[4] if len(imgs) > 4 else "",
                    "Description": item.get("description", ""),
                    "Status": item.get("status", "")
                })

            df = pd.DataFrame(flat_rows)
            out_buffer = io.BytesIO()
            with pd.ExcelWriter(out_buffer, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name="Enriched_Products")
                worksheet = writer.sheets["Enriched_Products"]
                from openpyxl.styles import Font, PatternFill, Alignment
                h_fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
                h_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
                for cell in worksheet[1]:
                    cell.fill = h_fill
                    cell.font = h_font
                for col in worksheet.columns:
                    col_letter = col[0].column_letter
                    worksheet.column_dimensions[col_letter].width = 20

            self.final_excel_bytes = out_buffer.getvalue()

        else:
            # Enriched CSV (Flat)
            flat_rows = []
            for item in self.live_output:
                imgs = item.get("images", [])
                r = dict(item.get("raw", {}))
                r["Enriched_Vendor"] = item.get("vendor", "")
                r["Enriched_Price"] = item.get("price", "")
                r["Enriched_MRP"] = item.get("mrp", "")
                r["Enriched_Tags"] = ", ".join(item.get("tags", []))
                r["Enriched_Image_1"] = imgs[0] if imgs else ""
                r["Enriched_Image_2"] = imgs[1] if len(imgs) > 1 else ""
                flat_rows.append(r)

            df = pd.DataFrame(flat_rows)
            self.final_csv_bytes = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")

    def get_status(self) -> Dict[str, Any]:
        with self.lock:
            elapsed = time.time() - self.start_time if self.is_running else 0
            return {
                "is_running": self.is_running,
                "progress": self.progress,
                "processed_count": self.processed_count,
                "total_to_process": self.total_to_process,
                "status_message": self.status_message,
                "elapsed_seconds": int(elapsed),
                "live_output_count": len(self.live_output),
                "live_output": self.live_output[-100:] if len(self.live_output) > 100 else self.live_output,
                "has_download": bool(self.final_csv_bytes or self.final_excel_bytes),
                "output_format": self.output_format
            }

    def get_export_file(self) -> tuple[bytes, str, str]:
        with self.lock:
            base = os.path.splitext(self.source_filename)[0] or "batch_enriched"
            if self.output_format == "excel":
                if self.final_excel_bytes:
                    return self.final_excel_bytes, f"{base}_enriched.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                raise Exception("Excel export is still being generated or machine was stopped early.")
            elif self.output_format == "flat_csv":
                if self.final_csv_bytes:
                    return self.final_csv_bytes, f"{base}_flat_enriched.csv", "text/csv"
                raise Exception("Flat CSV export is still being generated or machine was stopped early.")
            else:
                if self.final_csv_bytes:
                    return self.final_csv_bytes, f"{base}_shopify_ready.csv", "text/csv"
                raise Exception("Shopify CSV export is still being generated or machine was stopped early.")
