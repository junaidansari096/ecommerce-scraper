import csv
import os
import re
import shutil
import threading
from collections import defaultdict
from typing import Dict, List, Any, Optional

APP_DIR = os.path.dirname(os.path.abspath(__file__))

def _resolve_csv(filename: str) -> str:
    candidates = [
        os.path.join(APP_DIR, "data", filename),
        os.path.join(APP_DIR, filename),
        os.path.join(os.path.dirname(APP_DIR), filename),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return os.path.join(APP_DIR, "data", filename)

MASTER_CSV_PATH = _resolve_csv("all_products_master_enriched.csv")
PART1_CSV_PATH = _resolve_csv("all_products_part1_of_2.csv")
PART2_CSV_PATH = _resolve_csv("all_products_part2_of_2.csv")

class CatalogManager:
    def __init__(self, csv_path: str = MASTER_CSV_PATH):
        self.csv_path = csv_path
        self.lock = threading.RLock()
        self.fieldnames: List[str] = []
        self.products: Dict[str, List[Dict[str, str]]] = {}
        self.handles_order: List[str] = []
        self.load_catalog()

    def load_catalog(self):
        with self.lock:
            if not os.path.exists(self.csv_path):
                self.products = {}
                self.handles_order = []
                self.fieldnames = []
                return

            with open(self.csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                self.fieldnames = reader.fieldnames or []
                rows = list(reader)

            products = defaultdict(list)
            order = []
            for r in rows:
                h = r.get("URL handle", "").strip()
                if not h:
                    continue
                if h not in products:
                    order.append(h)
                products[h].append(r)

            self.products = dict(products)
            self.handles_order = order

    def save_catalog(self):
        with self.lock:
            temp_path = self.csv_path + ".tmp"
            all_rows = []
            for h in self.handles_order:
                if h in self.products:
                    all_rows.extend(self.products[h])

            with open(temp_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self.fieldnames)
                writer.writeheader()
                writer.writerows(all_rows)

            shutil.move(temp_path, self.csv_path)

    def get_stats(self) -> Dict[str, Any]:
        with self.lock:
            total_products = len(self.products)
            total_rows = sum(len(rows) for rows in self.products.values())
            
            prices = []
            vendors = defaultdict(int)
            tags_map = defaultdict(int)
            total_images = 0
            zero_price_count = 0
            amazon_vendor_count = 0
            tag_case_violations = 0

            for h, rows in self.products.items():
                p_row = rows[0]
                # Price
                try:
                    p = float(p_row.get("Price", 0) or 0)
                    if p > 0:
                        prices.append(p)
                    else:
                        zero_price_count += 1
                except:
                    zero_price_count += 1

                # Vendor
                v = p_row.get("Vendor", "").strip()
                if v:
                    vendors[v] += 1
                if v.lower() == "amazon":
                    amazon_vendor_count += 1

                # Tags
                tags_str = p_row.get("Tags", "")
                if tags_str:
                    for t in [x.strip() for x in tags_str.split(",") if x.strip()]:
                        tags_map[t] += 1
                        if t and not (t[0].isupper() and t[1:].islower()):
                            tag_case_violations += 1

                # Images
                img_count = sum(1 for r in rows if r.get("Product image URL", "").strip())
                total_images += img_count

            file_size_mb = 0.0
            if os.path.exists(self.csv_path):
                file_size_mb = round(os.path.getsize(self.csv_path) / (1024 * 1024), 2)

            avg_price = round(sum(prices) / max(len(prices), 1), 2)
            min_price = min(prices) if prices else 0
            max_price = max(prices) if prices else 0

            top_vendors = sorted(vendors.items(), key=lambda x: x[1], reverse=True)[:15]
            top_tags = sorted(tags_map.items(), key=lambda x: x[1], reverse=True)[:25]

            return {
                "total_products": total_products,
                "total_rows": total_rows,
                "total_images": total_images,
                "file_size_mb": file_size_mb,
                "avg_price": avg_price,
                "min_price": min_price,
                "max_price": max_price,
                "zero_price_count": zero_price_count,
                "amazon_vendor_count": amazon_vendor_count,
                "tag_case_violations": tag_case_violations,
                "top_vendors": [{"name": k, "count": v} for k, v in top_vendors],
                "top_tags": [{"name": k, "count": v} for k, v in top_tags],
                "is_shopify_ready": (zero_price_count == 0 and amazon_vendor_count == 0 and file_size_mb < 15.0)
            }

    def search_products(
        self,
        query: str = "",
        vendor: str = "",
        tag: str = "",
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        sort_by: str = "default",
        page: int = 1,
        page_size: int = 24
    ) -> Dict[str, Any]:
        with self.lock:
            q = query.strip().lower()
            v_filter = vendor.strip().lower()
            t_filter = tag.strip().lower()

            matched = []
            for h in self.handles_order:
                if h not in self.products:
                    continue
                rows = self.products[h]
                p_row = rows[0]

                # Query check
                if q:
                    title = p_row.get("Title", "").lower()
                    h_val = h.lower()
                    v_val = p_row.get("Vendor", "").lower()
                    tag_val = p_row.get("Tags", "").lower()
                    if q not in title and q not in h_val and q not in v_val and q not in tag_val:
                        continue

                # Vendor filter
                if v_filter:
                    if p_row.get("Vendor", "").lower() != v_filter:
                        continue

                # Tag filter
                if t_filter:
                    row_tags = [x.strip().lower() for x in p_row.get("Tags", "").split(",")]
                    if t_filter not in row_tags:
                        continue

                # Price filter
                try:
                    price = float(p_row.get("Price", 0) or 0)
                except:
                    price = 0.0

                if min_price is not None and price < min_price:
                    continue
                if max_price is not None and price > max_price:
                    continue

                images = [r.get("Product image URL", "") for r in rows if r.get("Product image URL", "")]
                matched.append({
                    "handle": h,
                    "title": p_row.get("Title", ""),
                    "vendor": p_row.get("Vendor", ""),
                    "price": price,
                    "compare_at_price": p_row.get("Compare-at price", ""),
                    "tags": [x.strip() for x in p_row.get("Tags", "").split(",") if x.strip()],
                    "status": p_row.get("Status", "Active"),
                    "image_count": len(images),
                    "primary_image": images[0] if images else "",
                    "images": images[:5]
                })

            # Sorting
            if sort_by == "title_asc":
                matched.sort(key=lambda x: x["title"].lower())
            elif sort_by == "title_desc":
                matched.sort(key=lambda x: x["title"].lower(), reverse=True)
            elif sort_by == "price_asc":
                matched.sort(key=lambda x: x["price"])
            elif sort_by == "price_desc":
                matched.sort(key=lambda x: x["price"], reverse=True)
            elif sort_by == "images_desc":
                matched.sort(key=lambda x: x["image_count"], reverse=True)

            total_items = len(matched)
            total_pages = max(1, (total_items + page_size - 1) // page_size)
            page = max(1, min(page, total_pages))
            start = (page - 1) * page_size
            end = start + page_size
            items = matched[start:end]

            return {
                "items": items,
                "total": total_items,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages
            }

    def get_product(self, handle: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            if handle not in self.products:
                return None
            rows = self.products[handle]
            p_row = rows[0]
            images = []
            for r in rows:
                img = r.get("Product image URL", "").strip()
                if img:
                    images.append({
                        "url": img,
                        "position": r.get("Image position", "1")
                    })

            return {
                "handle": handle,
                "title": p_row.get("Title", ""),
                "description": p_row.get("Description", ""),
                "vendor": p_row.get("Vendor", ""),
                "type": p_row.get("Type", ""),
                "tags": p_row.get("Tags", ""),
                "price": p_row.get("Price", ""),
                "compare_at_price": p_row.get("Compare-at price", ""),
                "status": p_row.get("Status", "Active"),
                "sku": p_row.get("SKU", ""),
                "barcode": p_row.get("Variant Barcodes", ""),
                "images": images
            }

    def update_product(self, handle: str, data: Dict[str, Any]) -> bool:
        with self.lock:
            if handle not in self.products:
                return False
            rows = self.products[handle]
            p_row = rows[0]

            if "title" in data and data["title"]:
                p_row["Title"] = str(data["title"]).strip()
            if "vendor" in data and data["vendor"]:
                p_row["Vendor"] = str(data["vendor"]).strip()
            if "price" in data:
                p_row["Price"] = str(data["price"]).strip()
            if "compare_at_price" in data:
                p_row["Compare-at price"] = str(data["compare_at_price"]).strip()
            if "tags" in data:
                p_row["Tags"] = str(data["tags"]).strip()
            if "status" in data:
                p_row["Status"] = str(data["status"]).strip()
            if "description" in data:
                p_row["Description"] = str(data["description"]).strip()

            self.save_catalog()
            return True

    def delete_product(self, handle: str) -> bool:
        with self.lock:
            if handle not in self.products:
                return False
            del self.products[handle]
            if handle in self.handles_order:
                self.handles_order.remove(handle)
            self.save_catalog()
            return True

    def run_split(self) -> Dict[str, Any]:
        with self.lock:
            handles = [h for h in self.handles_order if h in self.products]
            half = len(handles) // 2
            p1_handles = set(handles[:half])
            p2_handles = set(handles[half:])

            p1_rows = []
            p2_rows = []
            for h in handles:
                if h in p1_handles:
                    p1_rows.extend(self.products[h])
                else:
                    p2_rows.extend(self.products[h])

            with open(PART1_CSV_PATH, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self.fieldnames)
                writer.writeheader()
                writer.writerows(p1_rows)

            with open(PART2_CSV_PATH, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self.fieldnames)
                writer.writeheader()
                writer.writerows(p2_rows)

            size1 = round(os.path.getsize(PART1_CSV_PATH) / (1024 * 1024), 2)
            size2 = round(os.path.getsize(PART2_CSV_PATH) / (1024 * 1024), 2)

            return {
                "part1_products": len(p1_handles),
                "part1_rows": len(p1_rows),
                "part1_size_mb": size1,
                "part2_products": len(p2_handles),
                "part2_rows": len(p2_rows),
                "part2_size_mb": size2
            }
