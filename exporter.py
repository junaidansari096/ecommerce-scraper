import os
import io
import re
import csv
import pandas as pd
from typing import Dict, Any, List, Tuple
from project_db import ProjectDatabase, SHOPIFY_TEMPLATE_COLUMNS

SHOPIFY_61_COLUMNS = SHOPIFY_TEMPLATE_COLUMNS

class ProjectExporter:
    def __init__(self, db: ProjectDatabase):
        self.db = db

    def generate_export(self, project_id: str, fmt: str = "shopify_csv", scope: str = "all") -> Tuple[bytes, str, str]:
        project = self.db.get_project(project_id)
        if not project:
            raise ValueError(f"Project '{project_id}' not found.")

        rows = self.db.get_all_output_data(project_id)
        if not rows:
            # Fallback to input data if no outputs yet
            input_resp = self.db.get_input_rows(project_id, page=1, page_size=100000)
            rows = input_resp.get("rows", [])

        if scope == "completed":
            rows = [r for r in rows if r.get("__status__") == "completed"]
        elif scope == "failed":
            rows = [r for r in rows if r.get("__status__") == "failed"]

        if not rows:
            raise ValueError("No matching records found to export.")

        safe_name = re.sub(r'[^a-zA-Z0-9_\-]+', '_', project.get("name", "project")).strip('_') or "export"

        if fmt == "shopify_csv":
            all_csv_rows = []
            for item in rows:
                title = item.get("Title") or item.get("Product Name") or item.get("Name") or f"Product #{item.get('__row_idx__', 0) + 1}"
                handle = re.sub(r'[^a-zA-Z0-9]+', '-', title.lower()).strip('-')[:80]
                
                # Gather all images dynamically
                images = []
                for k in ["Primary_Image", "Product image URL"]:
                    if item.get(k) and item[k] not in images:
                        images.append(item[k])
                
                # Check Image_2 up to Image_20
                for i in range(2, 20):
                    k = f"Image_{i}"
                    if item.get(k) and item[k] not in images:
                        images.append(item[k])

                # Check if item has Images list
                if isinstance(item.get("Images"), list):
                    for u in item["Images"]:
                        if u and u not in images:
                            images.append(u)

                # Primary row
                p_row = {col: "" for col in SHOPIFY_61_COLUMNS}
                
                # Copy existing data keys matching Shopify columns
                for col in SHOPIFY_61_COLUMNS:
                    if col in item and item[col] is not None:
                        p_row[col] = str(item[col])

                # Ensure defaults for essential fields
                if not p_row.get("Title"):
                    p_row["Title"] = title
                if not p_row.get("URL handle"):
                    p_row["URL handle"] = handle
                if not p_row.get("Description"):
                    p_row["Description"] = item.get("Description", f"<p>{title}</p>")
                if not p_row.get("Vendor"):
                    p_row["Vendor"] = item.get("Vendor", "Generic")
                if not p_row.get("Tags"):
                    p_row["Tags"] = item.get("Tags", "")
                if not p_row.get("Price"):
                    p_row["Price"] = str(item.get("Price", "") or "")
                if not p_row.get("Compare-at price"):
                    p_row["Compare-at price"] = str(item.get("Compare_at_Price", "") or "")
                if p_row.get("Status") not in ["Active", "Draft", "Archived"]:
                    p_row["Status"] = "Active"
                if not p_row.get("Published on online store"):
                    p_row["Published on online store"] = "TRUE"
                if not p_row.get("Requires shipping"):
                    p_row["Requires shipping"] = "TRUE"
                if not p_row.get("Charge tax"):
                    p_row["Charge tax"] = "TRUE"
                if not p_row.get("Fulfillment service"):
                    p_row["Fulfillment service"] = "manual"
                if not p_row.get("Inventory tracker"):
                    p_row["Inventory tracker"] = "shopify"
                if not p_row.get("Inventory quantity"):
                    p_row["Inventory quantity"] = "50"
                if not p_row.get("Continue selling when out of stock"):
                    p_row["Continue selling when out of stock"] = "DENY"
                if not p_row.get("Weight unit for display"):
                    p_row["Weight unit for display"] = "g"
                if not p_row.get("Gift card"):
                    p_row["Gift card"] = "FALSE"
                if not p_row.get("Option1 name"):
                    p_row["Option1 name"] = "Title"
                if not p_row.get("Option1 value"):
                    p_row["Option1 value"] = "Default Title"
                if not p_row.get("SEO title"):
                    p_row["SEO title"] = title
                if not p_row.get("SEO description"):
                    clean_seo = re.sub(r'<[^>]+>', '', p_row["Description"]).strip()
                    p_row["SEO description"] = clean_seo[:320]

                if images:
                    p_row["Product image URL"] = images[0]
                    p_row["Image position"] = "1"
                    if not p_row.get("Image alt text"):
                        p_row["Image alt text"] = title

                all_csv_rows.append(p_row)

                # Secondary rows strictly 3 keys
                for idx, img_u in enumerate(images[1:], 2):
                    sec_r = {col: "" for col in SHOPIFY_61_COLUMNS}
                    sec_r["URL handle"] = handle
                    sec_r["Product image URL"] = img_u
                    sec_r["Image position"] = str(idx)
                    all_csv_rows.append(sec_r)

            out_buf = io.StringIO()
            writer = csv.DictWriter(out_buf, fieldnames=SHOPIFY_61_COLUMNS)
            writer.writeheader()
            writer.writerows(all_csv_rows)
            return out_buf.getvalue().encode("utf-8-sig"), f"{safe_name}_shopify_61col.csv", "text/csv"

        elif fmt == "excel":
            clean_rows = []
            for r in rows:
                c = {k: v for k, v in r.items() if not k.startswith("__")}
                clean_rows.append(c)

            df = pd.DataFrame(clean_rows)
            out_buffer = io.BytesIO()
            with pd.ExcelWriter(out_buffer, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name="Enriched_Products")
                worksheet = writer.sheets["Enriched_Products"]
                from openpyxl.styles import Font, PatternFill
                h_fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
                h_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
                for cell in worksheet[1]:
                    cell.fill = h_fill
                    cell.font = h_font
                for col in worksheet.columns:
                    col_letter = col[0].column_letter
                    worksheet.column_dimensions[col_letter].width = 22

            return out_buffer.getvalue(), f"{safe_name}_dataset.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

        else: # flat_csv
            clean_rows = []
            for r in rows:
                c = {k: v for k, v in r.items() if not k.startswith("__")}
                clean_rows.append(c)
            df = pd.DataFrame(clean_rows)
            csv_bytes = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
            return csv_bytes, f"{safe_name}_flat_dataset.csv", "text/csv"
