import os
import re
import sys
import json
import time
import urllib.parse
import subprocess
from typing import Dict, Any, List, Optional, Tuple

# Reconfigure stdout/stderr for Windows UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
if not os.path.exists(CHROME_PATH):
    CHROME_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

USER_DATA_DIR = os.path.join(os.path.dirname(__file__), "chrome_scraper_profile")
os.makedirs(USER_DATA_DIR, exist_ok=True)

APP_DIR = os.path.dirname(os.path.abspath(__file__))

def _resolve_data_file(filename: str) -> str:
    candidates = [
        os.path.join(APP_DIR, "data", filename),
        os.path.join(APP_DIR, filename),
        os.path.join(os.path.dirname(APP_DIR), filename),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return os.path.join(APP_DIR, "data", filename)

CACHE_PATH = _resolve_data_file("amazon_cache_v3.json")
MASTER_CSV_PATH = _resolve_data_file("all_products_master_enriched.csv")

KNOWN_BRANDS = [
    "7UP", "AreoVeda", "Britannia", "Bikano", "Bikaji", "Beauté Secrets", "Beaute Secrets",
    "Bisk Farm", "Bhujialalji", "Milton", "Cello", "boAt", "Philips", "Havells",
    "Hawkins", "Crompton", "Himalaya", "Bajaj", "Prestige", "Orient Electric",
    "Wipro", "Noise", "Usha", "Comfort", "Dabur", "Patanjali", "Nestle", "Cadbury",
    "Amul", "Tata", "Fortune", "Aashirvaad", "Saffola", "Parle", "Sunfeast", "Haldiram's",
    "Haldirams", "Lays", "Kurkure", "Dettol", "Lifebuoy", "Dove", "Nivea", "Pond's",
    "Garnier", "L'Oreal", "Colgate", "Sensodyne", "Oral-B", "Pampers", "Huggies",
    "MamyPoko", "Whisper", "Stayfree", "Sofy", "Surf Excel", "Ariel", "Tide", "Vim",
    "Pril", "Lizol", "Harpic", "Good Knight", "All Out", "Baygon", "Real", "Tropicana"
]

CATEGORY_TAG_RULES = [
    ("atta", "Atta"), ("flour", "Flour"), ("ceiling fan", "Ceiling fan"),
    ("exhaust fan", "Exhaust fan"), ("pedestal fan", "Pedestal fan"),
    ("led bulb", "Led bulb"), ("tubelight", "Tubelight"), ("smart bulb", "Smart bulb"),
    ("detergent", "Detergent powder"), ("liquid detergent", "Liquid detergent"),
    ("dishwash", "Dishwash"), ("soap", "Soap"), ("shampoo", "Shampoo"),
    ("biscuit", "Biscuits"), ("cookie", "Cookies"), ("namkeen", "Namkeen"),
    ("mixture", "Namkeen"), ("soft drink", "Beverages"), ("drink", "Beverages"),
    ("water bottle", "Water bottle"), ("flask", "Flask"), ("casserole", "Casserole"),
    ("pressure cooker", "Pressure cooker"), ("tawa", "Tawa"), ("kettle", "Electric kettle"),
    ("lipstick", "Lipstick"), ("foundation", "Foundation"), ("eyeliner", "Eyeliner"),
    ("sunscreen", "Sunscreen"), ("baby wash", "Baby care"), ("baby diaper", "Baby diaper"),
    ("baby cream", "Baby care"), ("baby oil", "Baby care"), ("bathing bar", "Baby care"),
    ("nail cutter", "Manicure & Pedicure"), ("manicure", "Manicure & Pedicure"),
    ("sanitary pads", "Sanitary pads"), ("panty liners", "Panty liners"),
    ("oats", "Oats"), ("tea", "Tea"), ("coffee", "Coffee"), ("peanut butter", "Peanut butter"),
    ("ghee", "Ghee"), ("butter", "Butter"), ("salt", "Salt"), ("sugar", "Sugar"),
    ("hair dryer", "Hair dryer"), ("trimmer", "Trimmer"), ("smart watch", "Smart watch"),
    ("earbuds", "Earbuds"), ("neckband", "Neckband")
]

class AmazonScraperEngine:
    def __init__(self):
        self.master_lookup: Dict[str, Dict[str, Any]] = {}
        self.cache: Dict[str, Dict[str, Any]] = {}
        self._load_local_catalog()
        self._load_cache()

    def _load_local_catalog(self):
        """Pre-index verified catalog products from all_products_master_enriched.csv."""
        if not os.path.exists(MASTER_CSV_PATH):
            return
        try:
            import pandas as pd
            df = pd.read_csv(MASTER_CSV_PATH, dtype=str).fillna("")
            current_entry = None
            for _, r in df.iterrows():
                title = r.get("Title", "").strip()
                img = r.get("Product image URL", "").strip()
                if title:
                    p_str = r.get("Price", "").strip()
                    try:
                        p_val = float(p_str) if p_str else 0.0
                    except Exception:
                        p_val = 0.0

                    tags = [t.strip() for t in r.get("Tags", "").split(",") if t.strip()]
                    clean_tags = []
                    for t in tags:
                        t_cap = t[0].upper() + t[1:].lower() if len(t) > 1 else t.upper()
                        if t_cap not in clean_tags:
                            clean_tags.append(t_cap)

                    vendor = r.get("Vendor", "").strip()
                    if not vendor or vendor.lower() in ["amazon", "generic"]:
                        vendor = self.resolve_brand_name(title)

                    current_entry = {
                        "title": title,
                        "vendor": vendor,
                        "price": str(p_val) if p_val > 0 else "",
                        "mrp": r.get("Compare-at price", str(round(p_val * 1.25)) if p_val > 0 else ""),
                        "tags": clean_tags,
                        "images": [img] if img else [],
                        "description": r.get("Description", f"<p>{title}</p>")
                    }
                    if title.lower() not in self.master_lookup:
                        self.master_lookup[title.lower()] = current_entry
                elif current_entry and img:
                    # Collect all secondary images for this product (Shopify CSV multi-row pattern)
                    if img not in current_entry["images"]:
                        current_entry["images"].append(img)
            print(f"[SCRAPER] Loaded {len(self.master_lookup)} master catalog items for instant lookup.")
        except Exception as e:
            print(f"[SCRAPER] Error loading master catalog: {e}")

    def _load_cache(self):
        """Load cached search and deep scrape results."""
        if os.path.exists(CACHE_PATH):
            try:
                with open(CACHE_PATH, "r", encoding="utf-8") as f:
                    self.cache = json.load(f)
                print(f"[SCRAPER] Loaded {len(self.cache)} cached items from amazon_cache_v3.json.")
            except Exception as e:
                print(f"[SCRAPER] Error loading cache: {e}")

    def _save_cache_item(self, key: str, data: Dict[str, Any]):
        try:
            self.cache[key] = data
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def clean_query(self, query: str) -> str:
        """Strip punctuation and normalize search query."""
        cq = re.sub(r'[,|()–—\/\+\*\!\?\[\]\"\'\:\;]+', ' ', query)
        return re.sub(r'\s+', ' ', cq).strip()

    def resolve_brand_name(self, title: str, page_vendor: str = "") -> str:
        """Resolve genuine brand name. Never 'Amazon', never empty."""
        if page_vendor:
            clean_pv = page_vendor.replace("Visit the", "").replace("Store", "").replace("Brand:", "").strip()
            if clean_pv and clean_pv.lower() not in ["amazon", "generic", "brand", "visit the store"]:
                return clean_pv

        title_lower = title.lower()
        for kb in KNOWN_BRANDS:
            if kb.lower() in title_lower:
                return kb

        words = title.split()
        if words:
            w = words[0].capitalize()
            if w.lower() not in ["the", "a", "an", "all", "pack", "new", "combo", "set", "original"]:
                return w
        return "Generic"

    def resolve_tags(self, title: str, vendor: str = "") -> List[str]:
        """Generate Title Case tags based on category rules."""
        tags = []
        if vendor and vendor != "Generic":
            tags.append(vendor)

        title_lower = title.lower()
        for kw, tag_val in CATEGORY_TAG_RULES:
            if kw in title_lower and tag_val not in tags:
                tags.append(tag_val)

        clean_tags = []
        for t in tags:
            if t:
                t_cap = t[0].upper() + t[1:].lower() if len(t) > 1 else t.upper()
                if t_cap not in clean_tags:
                    clean_tags.append(t_cap)
        return clean_tags

    def fetch_html(self, url: str, timeout: int = 22) -> str:
        """Fetch rendered HTML using Chrome headless in stealth profile."""
        if not os.path.exists(CHROME_PATH):
            return ""

        cmd = [
            CHROME_PATH,
            "--headless=new",
            "--disable-gpu",
            "--disable-extensions",
            "--disable-blink-features=AutomationControlled",
            f"--user-data-dir={USER_DATA_DIR}",
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "--window-size=1920,1080",
            "--lang=en-US,en",
            "--dump-dom",
            url
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=timeout)
            html = res.stdout or ""
            if any(k in html.lower() for k in ["captcha", "service unavailable error"]):
                return ""
            return html
        except Exception as e:
            print(f"[Chrome Scraper Error] {e}")
            return ""

    def estimate_price_by_reasoning(self, title: str, vendor: str = "") -> Tuple[float, float, str]:
        """
        Applies intelligent product reasoning to estimate retail price and compare-at MRP
        based on volume, weight, unit count, and category market benchmarks when live price is unlisted.
        """
        low = title.lower()

        # Extract numeric quantity and unit
        qty_match = re.search(r'(\d+(?:\.\d+)?)\s*(l|litre|liter|ltr|ml|kg|kilo|gram|gm|g|pcs|piece|pieces|pack|bar)', low)
        qty_val = float(qty_match.group(1)) if qty_match else None
        qty_unit = qty_match.group(2) if qty_match else None

        # 1. Soft Drinks / Beverages
        if any(k in low for k in ["soft drink", "soda", "cold drink", "cola", "pepsi", "coke", "7up", "sprite", "fanta", "mirinda", "limca", "thums up"]):
            if qty_val and qty_unit in ["l", "litre", "liter", "ltr"]:
                if qty_val >= 2.0:
                    return 99.0, 110.0, f"Beverage benchmark: {qty_val}L large family bottle"
                elif qty_val >= 1.0:
                    return 55.0, 65.0, f"Beverage benchmark: {qty_val}L bottle"
            elif qty_val and qty_unit == "ml":
                if qty_val >= 750:
                    return 40.0, 45.0, f"Beverage benchmark: {qty_val}ml bottle"
                elif qty_val >= 250:
                    return 35.0, 40.0, f"Beverage benchmark: {qty_val}ml can"
            return 99.0, 110.0, "Beverage category benchmark"

        # 2. Fruit Juices / Health drinks
        if any(k in low for k in ["juice", "tropicana", "real fruit", "maaza", "frooti", "slice"]):
            if qty_val and qty_unit in ["l", "litre", "liter", "ltr"] and qty_val >= 1.0:
                return round(120.0 * qty_val, 1), round(140.0 * qty_val, 1), f"Juice benchmark: {qty_val}L pack"
            return 125.0, 145.0, "Juice category benchmark"

        # 3. Biscuits & Cookies
        if any(k in low for k in ["biscuit", "cookie", "marie", "jim jam", "bourbon", "nutrichoice", "digestive", "parle-g", "good day", "milk bikis", "treat"]):
            if qty_val and qty_unit in ["kg", "kilo"]:
                return round(210.0 * qty_val, 1), round(250.0 * qty_val, 1), f"Biscuits benchmark: {qty_val}kg bulk pack"
            elif qty_val and qty_unit in ["g", "gm", "gram"]:
                if qty_val >= 800:
                    return 190.0, 230.0, f"Biscuits family pack ({qty_val}g)"
                elif qty_val >= 200:
                    return 45.0, 50.0, f"Biscuits standard pack ({qty_val}g)"
                elif qty_val >= 100:
                    return 35.0, 40.0, f"Biscuits medium pack ({qty_val}g)"
                else:
                    return 15.0, 20.0, f"Biscuits mini pack ({qty_val}g)"
            return 40.0, 45.0, "Biscuits category benchmark"

        # 4. Namkeen / Mixture / Snacks
        if any(k in low for k in ["mixture", "namkeen", "bhujia", "navratna", "sev", "snack", "bikano", "bikaji", "haldiram", "chana", "gathiya"]):
            if qty_val and qty_unit in ["kg", "kilo"]:
                return round(230.0 * qty_val, 1), round(280.0 * qty_val, 1), f"Namkeen mixture benchmark: {qty_val}kg pack"
            elif qty_val and qty_unit in ["g", "gm", "gram"]:
                if qty_val >= 800:
                    return 210.0, 260.0, f"Namkeen bulk pack ({qty_val}g)"
                elif qty_val >= 400:
                    return 115.0, 140.0, f"Namkeen medium pack ({qty_val}g)"
                else:
                    return 55.0, 70.0, f"Namkeen small pack ({qty_val}g)"
            return 230.0, 280.0, "Namkeen category benchmark"

        # 5. Baby Care & Toiletries
        if any(k in low for k in ["baby", "foam wash", "head-to-toe", "baby wash", "massage oil", "bathing bar", "diaper", "wipes"]):
            if "bathing bar" in low or "soap" in low:
                return 85.0, 110.0, "Baby bathing soap bar benchmark"
            if "massage oil" in low:
                if qty_val and qty_val >= 200:
                    return 349.0, 449.0, f"Baby massage oil ({qty_val}ml)"
                return 249.0, 329.0, "Baby massage oil 100ml benchmark"
            if "foam wash" in low or "wash" in low or "shampoo" in low:
                if qty_val and qty_val >= 400:
                    return 499.0, 649.0, f"Baby foam wash pump ({qty_val}ml)"
                return 229.0, 299.0, "Baby head-to-toe foam wash 100ml"
            return 249.0, 329.0, "Baby care category benchmark"

        # 6. Grooming & Tools
        if any(k in low for k in ["nail cutter", "nail clipper", "manicure", "pedicure", "scissors", "tweezers"]):
            if "10-in-1" in low or "10 in 1" in low:
                return 349.0, 599.0, "10-in-1 stainless steel manicure kit"
            if "8-in-1" in low or "8 in 1" in low:
                return 299.0, 499.0, "8-in-1 manicure kit"
            if "4pcs" in low or "4 pcs" in low:
                return 199.0, 349.0, "4-piece grooming set"
            if "2 pieces" in low or "2 pcs" in low or "set of 2" in low:
                return 149.0, 249.0, "2-piece nail clipper with catcher"
            return 129.0, 199.0, "Personal grooming tool benchmark"

        # 7. Detergents & Laundry
        if any(k in low for k in ["detergent", "washing powder", "surf", "aerial", "tide", "matic"]):
            if qty_val and qty_unit in ["kg", "kilo"]:
                return round(160.0 * qty_val, 1), round(195.0 * qty_val, 1), f"Detergent benchmark: {qty_val}kg"
            return 170.0, 210.0, "Detergent category benchmark"

        # 8. Tea & Coffee
        if any(k in low for k in ["tea", "chai", "coffee", "nescafe", "bru", "tata tea", "taj mahal"]):
            if qty_val and qty_unit in ["kg", "kilo"]:
                return round(450.0 * qty_val, 1), round(520.0 * qty_val, 1), f"Tea benchmark: {qty_val}kg"
            if qty_val and qty_unit in ["g", "gm", "gram"] and qty_val >= 400:
                return 230.0, 270.0, f"Tea pack ({qty_val}g)"
            return 130.0, 160.0, "Beverage dry pack benchmark"

        # 9. Generic Fallback based on weight / volume
        if qty_val and qty_unit in ["kg", "kilo"]:
            return round(240.0 * qty_val, 1), round(290.0 * qty_val, 1), f"Weight-based benchmark ({qty_val}kg)"
        if qty_val and qty_unit in ["l", "litre", "liter", "ltr"]:
            return round(130.0 * qty_val, 1), round(160.0 * qty_val, 1), f"Volume-based benchmark ({qty_val}L)"

        return 199.0, 249.0, "Standard e-commerce market median estimate"

    def clean_amazon_image_url(self, url: str) -> str:
        if not url:
            return ""
        return re.sub(r'\._[A-Za-z0-9_,]+_\.', '.', url)

    def extract_search_result(self, html: str) -> Optional[Dict[str, Any]]:
        asins = re.findall(r'data-asin="([B0][A-Z0-9]{9})"', html)
        seen = set()
        unique_asins = []
        for a in asins:
            if a not in seen and not a.startswith('000'):
                seen.add(a)
                unique_asins.append(a)

        results = []
        for asin in unique_asins[:8]:
            idx = html.find(f'data-asin="{asin}"')
            chunk = html[idx:idx+9000]

            is_sponsored = "Sponsored" in chunk[:300]

            img_m = re.search(r'src="(https://m\.media-amazon\.com/images/I/[^"\?]+\.(?:jpg|png|webp))"', chunk)
            clean_img = self.clean_amazon_image_url(img_m.group(1)) if img_m else ""

            title = ""
            h2_m = re.search(r'<h2[^>]*>.*?<span[^>]*>([^<]{5,})</span>', chunk, re.DOTALL)
            if h2_m:
                title = h2_m.group(1).strip()
            else:
                alt_m = re.search(r'alt="([^"]{5,})"', chunk)
                if alt_m:
                    title = alt_m.group(1).strip()

            price = ""
            price_m = re.search(r'<span class="a-price-whole">([^<]+)</span>', chunk)
            if price_m:
                price = price_m.group(1).replace(',', '').strip()

            mrp = ""
            mrp_m = re.search(r'data-a-strike="true"[^>]*>.*?<span class="a-offscreen">([^<]+)</span>', chunk, re.DOTALL)
            if mrp_m:
                mrp = re.sub(r'[^\d.]', '', mrp_m.group(1)).strip()
            else:
                mrp = price

            if clean_img and (price or title):
                results.append({
                    'asin': asin,
                    'title': title,
                    'price': price,
                    'mrp': mrp,
                    'primary_image': clean_img,
                    'sponsored': is_sponsored
                })

        organic = [r for r in results if not r['sponsored']]
        return organic[0] if organic else (results[0] if results else None)

    def lookup_local(self, product_name: str) -> Optional[Dict[str, Any]]:
        """Instant lookup in master catalog and cache before hitting network."""
        norm_name = product_name.strip()
        low_name = norm_name.lower()

        # 1. Exact title match in master catalog
        if low_name in self.master_lookup:
            rec = dict(self.master_lookup[low_name])
            rec["source"] = "Master Catalog (Exact)"
            return rec

        # 2. Token match in master catalog
        q_tokens = [t for t in self.clean_query(norm_name).lower().split() if len(t) > 2]
        if len(q_tokens) >= 2:
            best_match = None
            best_score = 0
            for k, v in self.master_lookup.items():
                match_count = sum(1 for t in q_tokens if t in k)
                score = match_count / len(q_tokens)
                if score >= 0.75 and score > best_score:
                    best_score = score
                    best_match = v

            if best_match:
                rec = dict(best_match)
                rec["source"] = "Master Catalog (Token Match)"
                return rec

        # 3. Cache lookup
        if norm_name in self.cache:
            rec = dict(self.cache[norm_name])
            rec["source"] = "Amazon Cache (Verified)"
            return rec

        return None

    def search_amazon_india(self, query: str) -> Optional[Dict[str, Any]]:
        """Search Amazon India with query sanitization and Chrome stealth."""
        clean_q = self.clean_query(query)
        if not clean_q:
            return None

        # 1. Search full cleaned query
        search_url = f"https://www.amazon.in/s?k={urllib.parse.quote(clean_q)}"
        search_html = self.fetch_html(search_url)

        # 2. If 0 results or blocked, retry with first 4-5 core keywords
        words = clean_q.split()
        if (not search_html or 'data-asin="' not in search_html) and len(words) > 3:
            short_q = " ".join(words[:4])
            search_url = f"https://www.amazon.in/s?k={urllib.parse.quote(short_q)}"
            search_html = self.fetch_html(search_url)

        if not search_html:
            return None

        basic_info = self.extract_search_result(search_html)
        if not basic_info:
            return None

        asin = basic_info['asin']
        return {
            "asin": asin,
            "url": f"https://www.amazon.in/dp/{asin}",
            "title": basic_info.get("title", query),
            "price": basic_info.get("price", ""),
            "mrp": basic_info.get("mrp", ""),
            "primary_image": basic_info.get("primary_image", "")
        }

    def scrape_product_details(self, url: str, rules: Dict[str, Any], search_fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Deep scrape product details with multi-image gallery and reliable field fallbacks."""
        m_asin = re.search(r'/dp/([B0][0-9A-Z]{9})', url)
        asin = m_asin.group(1) if m_asin else ""

        html = self.fetch_html(url)
        if not html and search_fallback:
            # Fallback to search result info
            v = self.resolve_brand_name(search_fallback.get("title", ""))
            return {
                "url": url,
                "asin": asin,
                "title": search_fallback.get("title", ""),
                "price": float(search_fallback.get("price") or 299),
                "compare_at_price": float(search_fallback.get("mrp") or 399),
                "vendor": v,
                "tags": self.resolve_tags(search_fallback.get("title", ""), v),
                "description_html": f"<p>{search_fallback.get('title', '')}</p>",
                "bullets": [],
                "images": [search_fallback.get("primary_image")] if search_fallback.get("primary_image") else []
            }

        result = {
            "url": url,
            "asin": asin,
            "title": "",
            "price": 0.0,
            "compare_at_price": 0.0,
            "vendor": "",
            "tags": [],
            "description_html": "",
            "bullets": [],
            "images": []
        }

        # 1. Title Extraction
        title_m = re.search(r'<span[^>]*id="productTitle"[^>]*>([^<]+)</span>', html)
        if title_m:
            result["title"] = title_m.group(1).strip()
        elif search_fallback:
            result["title"] = search_fallback.get("title", "")

        # 2. Images Extraction
        fetch_img = rules.get("add_images", rules.get("fetch_images", True))
        if fetch_img:
            max_img = int(rules.get("max_images", 5))
            product_images = []
            seen_ids = set()

            if search_fallback and search_fallback.get("primary_image"):
                p_img = search_fallback["primary_image"]
                m = re.search(r'images/I/([A-Za-z0-9_\-\+]+)\.', p_img)
                if m:
                    seen_ids.add(m.group(1))
                    product_images.append(p_img)

            container_start = html.find('id="imageBlock"')
            if container_start == -1:
                container_start = html.find('id="altImages"')
            if container_start == -1:
                container_start = html.find('"colorImages":')

            container = html[container_start:container_start+60000] if container_start != -1 else ""

            if container:
                dyn_imgs = re.findall(r'images/I/([A-Za-z0-9_\-\+]+)\.', container)
                for img_id in dyn_imgs:
                    if img_id not in seen_ids and len(img_id) > 6 and not any(k in img_id.lower() for k in ['play', 'video', 'icon', 'sprite']):
                        seen_ids.add(img_id)
                        product_images.append(f"https://m.media-amazon.com/images/I/{img_id}.jpg")

            result["images"] = product_images[:max_img]

        # 3. Pricing Extraction
        fetch_price = rules.get("add_pricing", rules.get("fetch_pricing", True))
        if fetch_price:
            price_val = 0.0
            p_match = re.search(r'<span class="a-price-whole">([0-9,]+)', html)
            if p_match:
                price_val = float(p_match.group(1).replace(",", ""))
            else:
                p_alt = re.search(r'corePriceDisplay_desktop_feature_div[\s\S]*?<span class="a-offscreen">₹?([0-9,.]+)', html)
                if p_alt:
                    price_val = float(re.sub(r'[^\d.]', '', p_alt.group(1)))
                elif search_fallback and search_fallback.get("price"):
                    try:
                        price_val = float(search_fallback["price"])
                    except Exception:
                        price_val = 0.0

            # If live price was not found on page, keep as 0.0 so the Reasoning Engine can evaluate user's toggle
            mrp_val = 0.0
            mrp_match = re.search(r'data-a-strike="true"[^>]*>.*?<span class="a-offscreen">([^<]+)</span>', html, re.DOTALL)
            if mrp_match:
                try:
                    mrp_val = float(re.sub(r'[^\d.]', '', mrp_match.group(1)))
                except Exception:
                    mrp_val = 0.0
            elif search_fallback and search_fallback.get("mrp"):
                try:
                    mrp_val = float(search_fallback["mrp"])
                except Exception:
                    mrp_val = 0.0

            result["price"] = str(price_val) if price_val > 0 else ""
            result["compare_at_price"] = str(mrp_val) if mrp_val > 0 else ""

        # 4. Brand / Vendor Extraction
        fetch_vendor = rules.get("add_vendor", rules.get("fetch_brand", rules.get("fetch_vendor", True)))
        if fetch_vendor:
            page_vendor = ""
            v_match = re.search(r'id="bylineInfo"[^>]*>(?:Brand:\s*|Visit the\s*)?([^<]+?)(?:\s*Store)?</a>', html)
            if v_match:
                page_vendor = v_match.group(1).strip()

            result["vendor"] = self.resolve_brand_name(result.get("title", ""), page_vendor)

        # 5. Category Tags
        fetch_tags = rules.get("add_tags", rules.get("fetch_tags", True))
        if fetch_tags:
            result["tags"] = self.resolve_tags(result.get("title", ""), result.get("vendor", ""))

        # 6. Description & Bullets
        fetch_desc = rules.get("add_description", rules.get("fetch_description", True))
        if fetch_desc:
            max_b = int(rules.get("max_bullets", 5))
            bullets = []
            bullet_section = re.findall(r'<div id="feature-bullets"[^>]*>.*?<ul[^>]*>(.*?)</ul>', html, re.DOTALL)
            if bullet_section:
                items = re.findall(r'<li[^>]*>.*?<span[^>]*class="a-list-item"[^>]*>(.*?)</span>', bullet_section[0], re.DOTALL)
                for b in items:
                    clean = re.sub(r'<[^>]+>', '', b).strip()
                    if clean and not clean.startswith('›') and len(clean) > 5:
                        clean = re.sub(r'(?i)\b(amazon|prime|warranty card)\b', '', clean).strip()
                        bullets.append(clean)
                        if len(bullets) >= max_b:
                            break

            result["bullets"] = bullets
            prod_desc = ""
            desc_m = re.search(r'<div id="productDescription"[^>]*>(.*?)</div>', html, re.DOTALL)
            if desc_m:
                raw_desc = desc_m.group(1)
                raw_desc = re.sub(r'<!--.*?-->', '', raw_desc, flags=re.DOTALL)
                raw_desc = re.sub(r'<script.*?</script>', '', raw_desc, flags=re.DOTALL)
                clean_desc = re.sub(r'<[^>]+>', ' ', raw_desc)
                clean_desc = re.sub(r'\s+', ' ', clean_desc).strip()
                if len(clean_desc) > 10:
                    prod_desc = clean_desc

            desc_parts = []
            if prod_desc:
                desc_parts.append(f"<p>{prod_desc}</p>")
            if bullets:
                desc_parts.append("<ul>")
                for b in bullets:
                    desc_parts.append(f"  <li>{b}</li>")
                desc_parts.append("</ul>")

            result["description_html"] = "\n".join(desc_parts) if desc_parts else f"<p>{result.get('title', '')}</p>"

        # Save to cache for future lookups
        if result["title"]:
            self._save_cache_item(result["title"], result)

        return result
