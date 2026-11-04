import sqlite3
import os
import re
import json
import time
from typing import Dict, Any, List, Optional, Tuple

DB_PATH = os.path.join(os.path.dirname(__file__), "projects.db")

SHOPIFY_TEMPLATE_COLUMNS = [
    "Title", "URL handle", "Description", "Vendor", "Product category", "Type", "Tags",
    "Published on online store", "Status", "SKU", "Variant Barcodes", "Option1 name",
    "Option1 value", "Option1 Linked To", "Option2 name", "Option2 value", "Option2 Linked To",
    "Option3 name", "Option3 value", "Option3 Linked To", "Price", "Compare-at price",
    "Cost per item", "Charge tax", "Tax code", "Unit price total measure", "Unit price total measure unit",
    "Unit price base measure", "Unit price base measure unit", "Inventory tracker",
    "Inventory quantity", "Continue selling when out of stock", "Weight value (grams)",
    "Weight unit for display", "Requires shipping", "Fulfillment service", "Product image URL",
    "Image position", "Image alt text", "Variant image URL", "Gift card", "SEO title", "SEO description",
    "Color (product.metafields.shopify.color-pattern)", "Google Shopping / Google product category",
    "Google Shopping / Gender", "Google Shopping / Age group", "Google Shopping / Manufacturer part number (MPN)",
    "Google Shopping / Ad group name", "Google Shopping / Ads labels", "Google Shopping / Condition",
    "Google Shopping / Custom product", "Google Shopping / Custom label 0", "Google Shopping / Custom label 1",
    "Google Shopping / Custom label 2", "Google Shopping / Custom label 3", "Google Shopping / Custom label 4",
    "Packed product length", "Packed product width", "Packed product height", "Packed product dimension unit"
]

class ProjectDatabase:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            cur = conn.cursor()
            
            # Projects Table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    platform TEXT DEFAULT 'amazon',
                    status TEXT DEFAULT 'draft',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    total_rows INTEGER DEFAULT 0,
                    processed_rows INTEGER DEFAULT 0,
                    failed_rows INTEGER DEFAULT 0,
                    concurrency INTEGER DEFAULT 2,
                    config_json TEXT DEFAULT '{}',
                    mapping_json TEXT DEFAULT '{}'
                )
            """)

            # Input Rows Table (For spreadsheet editor)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS project_inputs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    row_index INTEGER NOT NULL,
                    data_json TEXT NOT NULL,
                    mapped_target TEXT DEFAULT '',
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_inputs_proj_idx ON project_inputs(project_id, row_index)")

            # Project Columns Metadata
            cur.execute("""
                CREATE TABLE IF NOT EXISTS project_columns (
                    project_id TEXT NOT NULL,
                    col_type TEXT NOT NULL, -- 'input' or 'output'
                    columns_json TEXT NOT NULL,
                    PRIMARY KEY(project_id, col_type)
                )
            """)

            # Output Rows Table (Live-populated during scraping)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS project_outputs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    row_index INTEGER NOT NULL,
                    data_json TEXT NOT NULL,
                    status TEXT DEFAULT 'pending', -- pending, processing, completed, failed, needs_review
                    error_msg TEXT DEFAULT '',
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_outputs_proj_idx ON project_outputs(project_id, row_index)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_outputs_status ON project_outputs(project_id, status)")

            # Live Activity Logs Table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS project_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    worker_id TEXT DEFAULT 'system',
                    message TEXT NOT NULL,
                    level TEXT DEFAULT 'info',
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_logs_proj ON project_logs(project_id, id)")

            # Presets Table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS config_presets (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    config_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            
            conn.commit()

        # Insert standard presets if empty
        self._init_default_presets()

    def _init_default_presets(self):
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as count FROM config_presets")
            if cur.fetchone()["count"] == 0:
                now = time.time()
                presets = [
                    (
                        "preset_shopify_full",
                        "Shopify 61-Column Master",
                        "Extracts Brand, 5 Gallery Images, Price, MRP, Title Case Tags, and clean bullet descriptions formatted for Shopify CSV import.",
                        json.dumps({
                            "fetch_vendor": True,
                            "fetch_images": True,
                            "min_images": 2,
                            "max_images": 5,
                            "fetch_pricing": True,
                            "price_margin_pct": 0,
                            "round_price": True,
                            "fetch_description": True,
                            "max_bullets": 5,
                            "remove_html": False,
                            "fetch_tags": True,
                            "output_format": "shopify_csv"
                        }),
                        now
                    ),
                    (
                        "preset_catalog_audit",
                        "Catalog Price & Inventory Audit",
                        "Fast extraction of Brand, Retail Price, MRP, and Availability with 1 thumbnail for catalog price parity audits.",
                        json.dumps({
                            "fetch_vendor": True,
                            "fetch_images": True,
                            "min_images": 1,
                            "max_images": 1,
                            "fetch_pricing": True,
                            "price_margin_pct": 0,
                            "round_price": False,
                            "fetch_description": False,
                            "max_bullets": 0,
                            "remove_html": True,
                            "fetch_tags": True,
                            "output_format": "excel"
                        }),
                        now
                    ),
                    (
                        "preset_images_media",
                        "High-Res Gallery Extraction",
                        "Deep scrape extracting up to 10 high-resolution gallery images per product with vendor and product title.",
                        json.dumps({
                            "fetch_vendor": True,
                            "fetch_images": True,
                            "min_images": 3,
                            "max_images": 10,
                            "fetch_pricing": False,
                            "price_margin_pct": 0,
                            "round_price": False,
                            "fetch_description": False,
                            "max_bullets": 0,
                            "remove_html": True,
                            "fetch_tags": False,
                            "output_format": "flat_csv"
                        }),
                        now
                    )
                ]
                cur.executemany("INSERT INTO config_presets VALUES (?, ?, ?, ?, ?)", presets)
                conn.commit()

    # -------------------------------------------------------------
    # PROJECT CRUD
    # -------------------------------------------------------------
    def create_project(self, project_id: str, name: str, platform: str = "amazon", config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        now = time.time()
        cfg_str = json.dumps(config or {})
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO projects (id, name, platform, status, created_at, updated_at, config_json)
                VALUES (?, ?, ?, 'draft', ?, ?, ?)
            """, (project_id, name, platform, now, now, cfg_str))
            conn.commit()
        return self.get_project(project_id)

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
            row = cur.fetchone()
            if not row:
                return None
            p = dict(row)
            p["config"] = json.loads(p.get("config_json") or "{}")
            p["mapping"] = json.loads(p.get("mapping_json") or "{}")
            return p

    def list_projects(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM projects ORDER BY updated_at DESC")
            projects = []
            for r in cur.fetchall():
                p = dict(r)
                p["config"] = json.loads(p.get("config_json") or "{}")
                p["mapping"] = json.loads(p.get("mapping_json") or "{}")
                projects.append(p)
            return projects

    def update_project(self, project_id: str, updates: Dict[str, Any]) -> bool:
        allowed = ["name", "platform", "status", "total_rows", "processed_rows", "failed_rows", "concurrency", "config_json", "mapping_json"]
        fields = []
        vals = []
        for k, v in updates.items():
            if k in allowed:
                fields.append(f"{k} = ?")
                vals.append(json.dumps(v) if k in ["config_json", "mapping_json"] and not isinstance(v, str) else v)
        if not fields:
            return False
        fields.append("updated_at = ?")
        vals.append(time.time())
        vals.append(project_id)
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"UPDATE projects SET {', '.join(fields)} WHERE id = ?", vals)
            conn.commit()
            return cur.rowcount > 0

    def delete_project(self, project_id: str) -> bool:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM project_inputs WHERE project_id = ?", (project_id,))
            cur.execute("DELETE FROM project_outputs WHERE project_id = ?", (project_id,))
            cur.execute("DELETE FROM project_columns WHERE project_id = ?", (project_id,))
            cur.execute("DELETE FROM project_logs WHERE project_id = ?", (project_id,))
            cur.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            conn.commit()
            return cur.rowcount > 0

    # -------------------------------------------------------------
    # SPREADSHEET INPUT DATA MANAGEMENT
    # -------------------------------------------------------------
    def save_input_dataset(self, project_id: str, columns: List[str], rows: List[Dict[str, Any]], mapping: Optional[Dict[str, str]] = None) -> bool:
        now = time.time()
        with self._get_conn() as conn:
            cur = conn.cursor()
            # Clear old inputs for project
            cur.execute("DELETE FROM project_inputs WHERE project_id = ?", (project_id,))
            
            # Save columns metadata
            cur.execute("""
                INSERT OR REPLACE INTO project_columns (project_id, col_type, columns_json)
                VALUES (?, 'input', ?)
            """, (project_id, json.dumps(columns)))

            # Insert rows
            records = []
            target_col = (mapping or {}).get("target_column") or (columns[0] if columns else "")
            for idx, r in enumerate(rows):
                target_val = str(r.get(target_col, "")) if target_col else ""
                records.append((project_id, idx, json.dumps(r), target_val))

            cur.executemany("""
                INSERT INTO project_inputs (project_id, row_index, data_json, mapped_target)
                VALUES (?, ?, ?, ?)
            """, records)

            # Update project total_rows
            mapping_str = json.dumps(mapping or {})
            cur.execute("""
                UPDATE projects 
                SET total_rows = ?, mapping_json = ?, updated_at = ?
                WHERE id = ?
            """, (len(rows), mapping_str, now, project_id))

            conn.commit()
            return True

    def get_input_columns(self, project_id: str) -> List[str]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT columns_json FROM project_columns WHERE project_id = ? AND col_type = 'input'", (project_id,))
            row = cur.fetchone()
            if row:
                return json.loads(row["columns_json"])
            return []

    def set_input_columns(self, project_id: str, columns: List[str]):
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO project_columns (project_id, col_type, columns_json)
                VALUES (?, 'input', ?)
            """, (project_id, json.dumps(columns)))
            conn.commit()

    def get_input_rows(self, project_id: str, page: int = 1, page_size: int = 50, search: str = "", sort_col: str = "", sort_asc: bool = True) -> Dict[str, Any]:
        columns = self.get_input_columns(project_id)
        with self._get_conn() as conn:
            cur = conn.cursor()
            
            query = "SELECT row_index, data_json FROM project_inputs WHERE project_id = ?"
            params: List[Any] = [project_id]

            if search:
                query += " AND data_json LIKE ?"
                params.append(f"%{search}%")

            query += " ORDER BY row_index ASC"

            cur.execute(query, params)
            all_db_rows = cur.fetchall()
            total = len(all_db_rows)

            rows = []
            for r in all_db_rows:
                d = json.loads(r["data_json"])
                d["__row_idx__"] = r["row_index"]
                rows.append(d)

            # In-memory sorting if requested
            if sort_col and sort_col in columns:
                rows.sort(key=lambda x: str(x.get(sort_col, "")).lower(), reverse=not sort_asc)

            # Pagination
            start = (page - 1) * page_size
            end = start + page_size
            paginated = rows[start:end]

            total_pages = max(1, (total + page_size - 1) // page_size)

            return {
                "columns": columns,
                "rows": paginated,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages
            }

    def update_input_cell(self, project_id: str, row_idx: int, col_name: str, new_val: str) -> bool:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT data_json FROM project_inputs WHERE project_id = ? AND row_index = ?", (project_id, row_idx))
            row = cur.fetchone()
            if not row:
                return False
            data = json.loads(row["data_json"])
            data[col_name] = new_val
            cur.execute("UPDATE project_inputs SET data_json = ? WHERE project_id = ? AND row_index = ?", (json.dumps(data), project_id, row_idx))
            conn.commit()
            return True

    def add_input_column(self, project_id: str, new_col: str, default_val: str = "") -> bool:
        cols = self.get_input_columns(project_id)
        if new_col in cols:
            return False
        cols.append(new_col)
        self.set_input_columns(project_id, cols)
        # Update all rows
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT row_index, data_json FROM project_inputs WHERE project_id = ?", (project_id,))
            rows = cur.fetchall()
            updates = []
            for r in rows:
                d = json.loads(r["data_json"])
                d[new_col] = default_val
                updates.append((json.dumps(d), project_id, r["row_index"]))
            cur.executemany("UPDATE project_inputs SET data_json = ? WHERE project_id = ? AND row_index = ?", updates)
            conn.commit()
            return True

    def delete_input_column(self, project_id: str, col_to_del: str) -> bool:
        cols = self.get_input_columns(project_id)
        if col_to_del not in cols:
            return False
        cols.remove(col_to_del)
        self.set_input_columns(project_id, cols)
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT row_index, data_json FROM project_inputs WHERE project_id = ?", (project_id,))
            rows = cur.fetchall()
            updates = []
            for r in rows:
                d = json.loads(r["data_json"])
                d.pop(col_to_del, None)
                updates.append((json.dumps(d), project_id, r["row_index"]))
            cur.executemany("UPDATE project_inputs SET data_json = ? WHERE project_id = ? AND row_index = ?", updates)
            conn.commit()
            return True

    def rename_input_column(self, project_id: str, old_col: str, new_col: str) -> bool:
        cols = self.get_input_columns(project_id)
        if old_col not in cols or new_col in cols:
            return False
        idx = cols.index(old_col)
        cols[idx] = new_col
        self.set_input_columns(project_id, cols)
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT row_index, data_json FROM project_inputs WHERE project_id = ?", (project_id,))
            rows = cur.fetchall()
            updates = []
            for r in rows:
                d = json.loads(r["data_json"])
                if old_col in d:
                    d[new_col] = d.pop(old_col)
                updates.append((json.dumps(d), project_id, r["row_index"]))
            cur.executemany("UPDATE project_inputs SET data_json = ? WHERE project_id = ? AND row_index = ?", updates)
            conn.commit()
            return True

    def add_input_row(self, project_id: str, row_data: Dict[str, Any]) -> int:
        cols = self.get_input_columns(project_id)
        # Ensure all columns exist
        clean = {c: str(row_data.get(c, "")) for c in cols}
        now = time.time()
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT MAX(row_index) as max_idx FROM project_inputs WHERE project_id = ?", (project_id,))
            row = cur.fetchone()
            new_idx = (row["max_idx"] + 1) if row and row["max_idx"] is not None else 0
            cur.execute("""
                INSERT INTO project_inputs (project_id, row_index, data_json)
                VALUES (?, ?, ?)
            """, (project_id, new_idx, json.dumps(clean)))
            cur.execute("UPDATE projects SET total_rows = total_rows + 1, updated_at = ? WHERE id = ?", (now, project_id))
            conn.commit()
            return new_idx

    def delete_input_rows(self, project_id: str, row_indices: List[int]) -> bool:
        if not row_indices:
            return False
        with self._get_conn() as conn:
            cur = conn.cursor()
            placeholders = ",".join(["?"] * len(row_indices))
            cur.execute(f"DELETE FROM project_inputs WHERE project_id = ? AND row_index IN ({placeholders})", [project_id] + row_indices)
            
            # Re-index remaining rows for clean sequential numbering
            cur.execute("SELECT id, row_index FROM project_inputs WHERE project_id = ? ORDER BY row_index ASC", (project_id,))
            remaining = cur.fetchall()
            updates = [(new_idx, r["id"]) for new_idx, r in enumerate(remaining)]
            cur.executemany("UPDATE project_inputs SET row_index = ? WHERE id = ?", updates)
            
            cur.execute("UPDATE projects SET total_rows = ?, updated_at = ? WHERE id = ?", (len(remaining), time.time(), project_id))
            conn.commit()
            return True

    # -------------------------------------------------------------
    # LIVE OUTPUT STREAMING DATA MANAGEMENT
    # -------------------------------------------------------------
    def get_default_output_columns(self, max_images: int = 5) -> List[str]:
        cols = ["Title", "Vendor", "Price", "Compare_at_Price", "Tags", "Primary_Image"]
        for i in range(2, max_images + 1):
            cols.append(f"Image_{i}")
        cols.extend(["Description", "Status"])
        return cols

    def get_output_columns(self, project_id: str) -> List[str]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT config_json FROM projects WHERE id = ?", (project_id,))
            p_row = cur.fetchone()
            cfg = {}
            if p_row and p_row["config_json"]:
                try:
                    cfg = json.loads(p_row["config_json"])
                except Exception:
                    cfg = {}

            max_images = int(cfg.get("max_images", 5))

            if cfg.get("shopify_template_mode", False):
                return SHOPIFY_TEMPLATE_COLUMNS

            cur.execute("SELECT columns_json FROM project_columns WHERE project_id = ? AND col_type = 'output'", (project_id,))
            row = cur.fetchone()
            if row:
                cols = json.loads(row["columns_json"])
                # If this project is already in Shopify template mode, return all 61 columns directly
                if "URL handle" in cols and "Packed product dimension unit" in cols:
                    return cols
            else:
                cols = self.get_default_output_columns(max_images)

            # DYNAMIC EXTENSION: IF USER SELECTED A FIELD IN CONFIG NOT PRESENT IN SHEET, CREATE IT!
            if cfg.get("fetch_vendor", True) and "Vendor" not in cols:
                cols.insert(1 if len(cols) > 1 else 0, "Vendor")

            if cfg.get("fetch_pricing", True):
                if "Price" not in cols:
                    cols.insert(2 if len(cols) > 2 else len(cols), "Price")
                if "Compare_at_Price" not in cols and "Compare-at price" not in cols:
                    cols.insert(3 if len(cols) > 3 else len(cols), "Compare_at_Price")

            if cfg.get("fetch_tags", True) and "Tags" not in cols:
                # Insert Tags right before Images or after Pricing
                target_idx = cols.index("Primary_Image") if "Primary_Image" in cols else (len(cols) - 1)
                cols.insert(max(0, target_idx), "Tags")

            if cfg.get("fetch_images", True):
                if "Primary_Image" not in cols:
                    cols.append("Primary_Image")
                for i in range(2, max_images + 1):
                    img_col = f"Image_{i}"
                    if img_col not in cols:
                        if "Description" in cols:
                            cols.insert(cols.index("Description"), img_col)
                        else:
                            cols.append(img_col)

            if cfg.get("fetch_description", True) and "Description" not in cols:
                if "Status" in cols:
                    cols.insert(cols.index("Status"), "Description")
                else:
                    cols.append("Description")

            if "Status" not in cols:
                cols.append("Status")

            return cols

    def apply_shopify_schema(self, project_id: str) -> List[str]:
        """
        Re-creates the output spreadsheet according to the official 61-column Shopify Product CSV template.
        Maps existing extracted fields and auto-populates Shopify standard values.
        """
        with self._get_conn() as conn:
            cur = conn.cursor()
            # 1. Update project columns
            cur.execute("""
                INSERT OR REPLACE INTO project_columns (project_id, col_type, columns_json)
                VALUES (?, 'output', ?)
            """, (project_id, json.dumps(SHOPIFY_TEMPLATE_COLUMNS)))

            # 2. Re-map all existing output rows to official Shopify 61-column schema
            cur.execute("SELECT row_index, data_json, status, error_msg FROM project_outputs WHERE project_id = ?", (project_id,))
            rows = cur.fetchall()
            for r in rows:
                row_idx = r["row_index"]
                data = json.loads(r["data_json"])
                title = data.get("Title") or data.get("Product Name") or data.get("Name") or f"Product #{row_idx + 1}"
                handle = re.sub(r'[^a-zA-Z0-9]+', '-', title.lower()).strip('-')[:80]
                desc = data.get("Description", f"<p>{title}</p>")
                vendor = data.get("Vendor", "Generic")
                tags = data.get("Tags", "")
                price = str(data.get("Price", "") or "")
                compare_price = str(data.get("Compare_at_Price", "") or data.get("Compare-at price", "") or "")
                img1 = data.get("Primary_Image") or data.get("Product image URL") or ""

                mapped = {col: "" for col in SHOPIFY_TEMPLATE_COLUMNS}
                mapped["Title"] = title
                mapped["URL handle"] = handle
                mapped["Description"] = desc
                mapped["Vendor"] = vendor
                mapped["Product category"] = data.get("Product category") or data.get("Category", "")
                mapped["Type"] = data.get("Type") or data.get("Category", "")
                mapped["Tags"] = tags
                mapped["Published on online store"] = "TRUE"
                mapped["Status"] = "Active"
                mapped["SKU"] = data.get("SKU") or f"SKU-{row_idx + 1:04d}"
                mapped["Option1 name"] = "Title"
                mapped["Option1 value"] = "Default Title"
                mapped["Price"] = price
                mapped["Compare-at price"] = compare_price
                mapped["Charge tax"] = "TRUE"
                mapped["Requires shipping"] = "TRUE"
                mapped["Fulfillment service"] = "manual"
                mapped["Inventory tracker"] = "shopify"
                mapped["Inventory quantity"] = "50"
                mapped["Continue selling when out of stock"] = "DENY"
                mapped["Weight value (grams)"] = str(data.get("Weight value (grams)", "150"))
                mapped["Weight unit for display"] = "g"
                mapped["Product image URL"] = img1
                mapped["Image position"] = "1"
                mapped["Image alt text"] = title
                mapped["Gift card"] = "FALSE"
                mapped["SEO title"] = title
                mapped["SEO description"] = re.sub(r'<[^>]+>', '', desc)[:320]

                # Preserve any existing Shopify fields already set
                for k in SHOPIFY_TEMPLATE_COLUMNS:
                    if k == "Status":
                        if str(data.get("Status")).strip() in ["Active", "Draft", "Archived"]:
                            mapped["Status"] = data["Status"]
                        else:
                            mapped["Status"] = "Active"
                    elif k in data and data[k]:
                        mapped[k] = data[k]

                # Preserve gallery images & system fields for multi-row export
                for i in range(2, 21):
                    k = f"Image_{i}"
                    if k in data and data[k]:
                        mapped[k] = data[k]
                if "Images" in data and isinstance(data["Images"], list):
                    mapped["Images"] = data["Images"]
                if "Primary_Image" in data and data["Primary_Image"]:
                    mapped["Primary_Image"] = data["Primary_Image"]

                # Keep system keys
                mapped["__row_idx__"] = row_idx
                mapped["__status__"] = r["status"]
                mapped["__error__"] = r["error_msg"]

                cur.execute("""
                    UPDATE project_outputs 
                    SET data_json = ?, updated_at = ?
                    WHERE project_id = ? AND row_index = ?
                """, (json.dumps(mapped), time.time(), project_id, row_idx))

            conn.commit()
            return SHOPIFY_TEMPLATE_COLUMNS

    def add_missing_shopify_columns(self, project_id: str) -> List[str]:
        """
        Keeps all existing spreadsheet columns and appends any missing Shopify template columns.
        """
        cols = list(self.get_output_columns(project_id))
        for sc in SHOPIFY_TEMPLATE_COLUMNS:
            if sc not in cols:
                cols.append(sc)
        self.set_output_columns(project_id, cols)
        return cols

    def reset_studio_columns(self, project_id: str) -> List[str]:
        """Resets the output view to the clean operational Studio columns."""
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT config_json FROM projects WHERE id = ?", (project_id,))
            p_row = cur.fetchone()
            max_img = 5
            if p_row and p_row["config_json"]:
                try:
                    cfg = json.loads(p_row["config_json"])
                    max_img = int(cfg.get("max_images", 5))
                except Exception:
                    pass
            cols = self.get_default_output_columns(max_img)
            self.set_output_columns(project_id, cols)
            return cols

    def set_output_columns(self, project_id: str, columns: List[str]):
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO project_columns (project_id, col_type, columns_json)
                VALUES (?, 'output', ?)
            """, (project_id, json.dumps(columns)))
            conn.commit()

    def save_output_row(self, project_id: str, row_idx: int, data: Dict[str, Any], status: str = "completed", error_msg: str = ""):
        now = time.time()
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO project_outputs (project_id, row_index, data_json, status, error_msg, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (project_id, row_idx, json.dumps(data), status, error_msg, now))
            
            # Update project counters
            cur.execute("SELECT COUNT(*) as completed FROM project_outputs WHERE project_id = ? AND status = 'completed'", (project_id,))
            completed = cur.fetchone()["completed"]
            cur.execute("SELECT COUNT(*) as failed FROM project_outputs WHERE project_id = ? AND status = 'failed'", (project_id,))
            failed = cur.fetchone()["failed"]

            cur.execute("""
                UPDATE projects 
                SET processed_rows = ?, failed_rows = ?, updated_at = ?
                WHERE id = ?
            """, (completed, failed, now, project_id))
            conn.commit()

    def get_output_rows(self, project_id: str, page: int = 1, page_size: int = 50, search: str = "", status_filter: str = "") -> Dict[str, Any]:
        columns = self.get_output_columns(project_id)
        with self._get_conn() as conn:
            cur = conn.cursor()
            query = "SELECT row_index, data_json, status, error_msg, updated_at FROM project_outputs WHERE project_id = ?"
            params: List[Any] = [project_id]

            if status_filter:
                query += " AND status = ?"
                params.append(status_filter)

            if search:
                query += " AND data_json LIKE ?"
                params.append(f"%{search}%")

            query += " ORDER BY row_index ASC"

            cur.execute(query, params)
            all_rows = cur.fetchall()
            total = len(all_rows)

            rows = []
            for r in all_rows:
                d = json.loads(r["data_json"])
                d["__row_idx__"] = r["row_index"]
                d["__status__"] = r["status"]
                d["__error__"] = r["error_msg"]
                rows.append(d)

            start = (page - 1) * page_size
            end = start + page_size
            paginated = rows[start:end]
            total_pages = max(1, (total + page_size - 1) // page_size)

            return {
                "columns": columns,
                "rows": paginated,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages
            }

    def update_output_cell(self, project_id: str, row_idx: int, col_name: str, new_val: str) -> bool:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT data_json FROM project_outputs WHERE project_id = ? AND row_index = ?", (project_id, row_idx))
            row = cur.fetchone()
            if not row:
                return False
            data = json.loads(row["data_json"])
            data[col_name] = new_val
            cur.execute("UPDATE project_outputs SET data_json = ?, updated_at = ? WHERE project_id = ? AND row_index = ?", (json.dumps(data), time.time(), project_id, row_idx))
            conn.commit()
            return True

    def get_all_output_data(self, project_id: str) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT row_index, data_json, status, error_msg FROM project_outputs WHERE project_id = ? ORDER BY row_index ASC", (project_id,))
            rows = []
            for r in cur.fetchall():
                d = json.loads(r["data_json"])
                d["__row_idx__"] = r["row_index"]
                d["__status__"] = r["status"]
                d["__error__"] = r["error_msg"]
                rows.append(d)
            return rows

    # -------------------------------------------------------------
    # LOGS & PRESETS
    # -------------------------------------------------------------
    def add_log(self, project_id: str, worker_id: str, message: str, level: str = "info"):
        now = time.time()
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO project_logs (project_id, timestamp, worker_id, message, level)
                VALUES (?, ?, ?, ?, ?)
            """, (project_id, now, worker_id, message, level))
            conn.commit()

    def get_logs(self, project_id: str, since_id: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, timestamp, worker_id, message, level 
                FROM project_logs 
                WHERE project_id = ? AND id > ? 
                ORDER BY id ASC LIMIT ?
            """, (project_id, since_id, limit))
            logs = []
            for r in cur.fetchall():
                t_str = time.strftime("%H:%M:%S", time.localtime(r["timestamp"]))
                logs.append({
                    "id": r["id"],
                    "time": t_str,
                    "worker": r["worker_id"],
                    "message": r["message"],
                    "level": r["level"]
                })
            return logs

    def get_presets(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM config_presets ORDER BY created_at ASC")
            presets = []
            for r in cur.fetchall():
                p = dict(r)
                p["config"] = json.loads(p.get("config_json") or "{}")
                presets.append(p)
            return presets

    def save_preset(self, preset_id: str, name: str, description: str, config: Dict[str, Any]) -> bool:
        now = time.time()
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO config_presets (id, name, description, config_json, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (preset_id, name, description, json.dumps(config), now))
            conn.commit()
            return True

    def get_dashboard_stats(self) -> Dict[str, Any]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as total_projects FROM projects")
            tot_p = cur.fetchone()["total_projects"]

            cur.execute("SELECT COUNT(*) as active_projects FROM projects WHERE status IN ('running', 'processing')")
            act_p = cur.fetchone()["active_projects"]

            cur.execute("SELECT COUNT(*) as completed_projects FROM projects WHERE status = 'completed'")
            comp_p = cur.fetchone()["completed_projects"]

            cur.execute("SELECT COALESCE(SUM(processed_rows), 0) as total_extracted FROM projects")
            tot_extracted = cur.fetchone()["total_extracted"]

            cur.execute("SELECT * FROM projects ORDER BY updated_at DESC LIMIT 5")
            recent = [dict(r) for r in cur.fetchall()]

            cur.execute("SELECT * FROM project_logs ORDER BY id DESC LIMIT 10")
            recent_logs = []
            for r in cur.fetchall():
                recent_logs.append({
                    "id": r["id"],
                    "time": time.strftime("%H:%M:%S", time.localtime(r["timestamp"])),
                    "project_id": r["project_id"],
                    "worker": r["worker_id"],
                    "message": r["message"],
                    "level": r["level"]
                })

            return {
                "total_projects": tot_p,
                "active_projects": act_p,
                "completed_projects": comp_p,
                "total_extracted_records": tot_extracted,
                "recent_projects": recent,
                "recent_activity": recent_logs
            }
