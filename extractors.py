import os
import re
import sys
import json
import time
import urllib.parse
import subprocess
from typing import Dict, Any, List, Optional

# Ensure stdout reconfigured
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from scraper_engine import AmazonScraperEngine

def build_output_result(
    title: str,
    vendor: str,
    price: str,
    mrp: str,
    tags: Any,
    images: List[str],
    desc: str,
    status: str,
    source_url: str,
    rules: Dict[str, Any],
    engine: AmazonScraperEngine
) -> Dict[str, Any]:
    """
    Standardized result builder that:
    1. Dynamically populates Image columns up to rules['max_images'] (Image_2, Image_3, Image_4, Image_5, etc.).
    2. Respects the 'estimate_missing_price' toggle:
       - If ON and price cannot be found, assigns an approximate price determined by the system's reasoning.
       - If OFF and price cannot be found, leaves price empty and tags as Price Unlisted.
    3. Formats tags into Title Case.
    4. Applies margin markup percentage and rounding.
    """
    # 1. Images formatting
    max_img = int(rules.get("max_images", 5))
    fetch_img = rules.get("fetch_images", True)
    
    valid_images = [img for img in images if img] if fetch_img else []
    selected_images = valid_images[:max_img]
    primary_img = selected_images[0] if len(selected_images) > 0 else ""

    # 2. Pricing & Approximate Price Reasoning
    fetch_price = rules.get("fetch_pricing", True)
    estimate_missing = rules.get("estimate_missing_price", rules.get("approximate_pricing", False))
    
    has_valid_price = False
    try:
        if price:
            has_valid_price = float(price) > 0
    except Exception:
        has_valid_price = False

    status_suffix = ""
    if fetch_price:
        if not has_valid_price:
            if estimate_missing:
                # System reasoning-based approximate price
                est_p, est_mrp, reason = engine.estimate_price_by_reasoning(title, vendor)
                price = str(est_p)
                mrp = str(est_mrp)
                status_suffix = " (Estimated ~)"
            else:
                price = ""
                mrp = ""
                status_suffix = " (Price Unlisted)"
        
        # Apply margin markup & rounding if price exists
        if price:
            try:
                p_val = float(price)
                margin_pct = float(rules.get("price_margin_pct", 0))
                if margin_pct > 0:
                    p_val = p_val * (1 + margin_pct / 100.0)
                
                if rules.get("round_price", False):
                    price = str(round(p_val))
                else:
                    price = f"{p_val:.2f}"
            except Exception:
                pass
    else:
        price = ""
        mrp = ""

    # 3. Vendor check
    if not rules.get("fetch_vendor", True):
        vendor = ""
    elif not vendor or vendor.lower() in ["amazon", "generic"]:
        vendor = engine.resolve_brand_name(title)

    # 4. Tags in Title Case
    tag_str = ""
    if rules.get("fetch_tags", True):
        if isinstance(tags, list):
            tag_str = ", ".join(tags)
        else:
            tag_str = str(tags or "")
        if rules.get("tags_title_case", True) and tag_str:
            tag_str = ", ".join([t.strip().title() for t in tag_str.split(",") if t.strip()])

    # 5. Description
    if not rules.get("fetch_description", True):
        desc = ""
    elif not desc:
        desc = f"<p><strong>{title}</strong></p><p>Authentic quality product sourced from verified supplier.</p>"

    result = {
        "Title": title,
        "Vendor": vendor,
        "Price": price,
        "Compare_at_Price": mrp,
        "Tags": tag_str,
        "Primary_Image": primary_img,
        "Images": selected_images,
        "Description": desc,
        "Status": f"{status}{status_suffix}",
        "Source_URL": source_url
    }

    # Dynamically populate Image_2, Image_3, Image_4, Image_5, ... up to max_img
    for i in range(2, max_img + 1):
        result[f"Image_{i}"] = selected_images[i - 1] if len(selected_images) >= i else ""

    return result

class BaseExtractor:
    def __init__(self, platform_id: str, name: str):
        self.platform_id = platform_id
        self.name = name

    def extract(self, query_or_url: str, rules: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

class AmazonExtractorAdapter(BaseExtractor):
    def __init__(self):
        super().__init__("amazon", "Amazon India & Global")
        self.engine = AmazonScraperEngine()

    def extract(self, query_or_url: str, rules: Dict[str, Any]) -> Dict[str, Any]:
        target = query_or_url.strip()
        if not target:
            raise ValueError("Empty product name or URL")

        # 1. Tier 1: Check instant local verified catalog
        local_match = self.engine.lookup_local(target)
        if local_match:
            return build_output_result(
                title=local_match.get("title") or target,
                vendor=local_match.get("vendor", ""),
                price=str(local_match.get("price", "")),
                mrp=str(local_match.get("mrp", "")),
                tags=local_match.get("tags", []),
                images=local_match.get("images", []),
                desc=local_match.get("description", ""),
                status="Verified ✅",
                source_url=target,
                rules=rules,
                engine=self.engine
            )

        # 2. Tier 2: Live search or direct URL scrape
        if target.startswith("http"):
            details = self.engine.scrape_product_details(target, rules)
            return build_output_result(
                title=details.get("title") or target,
                vendor=details.get("vendor", ""),
                price=str(details.get("price", "")),
                mrp=str(details.get("compare_at_price", "")),
                tags=details.get("tags", []),
                images=details.get("images", []),
                desc=details.get("description_html", ""),
                status="Enriched ✅",
                source_url=target,
                rules=rules,
                engine=self.engine
            )
        else:
            search_res = self.engine.search_amazon_india(target)
            if not search_res:
                v = self.engine.resolve_brand_name(target)
                t = self.engine.resolve_tags(target, v)
                return build_output_result(
                    title=target,
                    vendor=v,
                    price="",
                    mrp="",
                    tags=t,
                    images=[],
                    desc=f"<p>{target}</p>",
                    status="Unmatched (Brand Tagged)",
                    source_url=target,
                    rules=rules,
                    engine=self.engine
                )

            details = self.engine.scrape_product_details(search_res["url"], rules, search_fallback=search_res)
            return build_output_result(
                title=details.get("title") or target,
                vendor=details.get("vendor", ""),
                price=str(details.get("price", "")),
                mrp=str(details.get("compare_at_price", "")),
                tags=details.get("tags", []),
                images=details.get("images", []),
                desc=details.get("description_html", ""),
                status="Enriched ✅",
                source_url=search_res.get("url", target),
                rules=rules,
                engine=self.engine
            )

class ShopifyExtractorAdapter(BaseExtractor):
    def __init__(self):
        super().__init__("shopify", "Shopify Storefronts")
        self.engine = AmazonScraperEngine()

    def extract(self, query_or_url: str, rules: Dict[str, Any]) -> Dict[str, Any]:
        import requests
        url = query_or_url.strip()
        if not url.startswith("http"):
            raise ValueError(f"Shopify extraction requires a valid product URL, got: {url}")

        json_url = url.split("?")[0].rstrip("/") + ".json"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/html"
        }

        title = ""
        vendor = ""
        price = ""
        compare_price = ""
        tags = []
        images = []
        desc = ""

        try:
            r = requests.get(json_url, headers=headers, timeout=12)
            if r.status_code == 200:
                p_data = r.json().get("product", {})
                title = p_data.get("title", "")
                vendor = p_data.get("vendor", "")
                desc = p_data.get("body_html", "")
                t_raw = p_data.get("tags", "")
                tags = [t.strip() for t in t_raw.split(",") if t.strip()] if isinstance(t_raw, str) else t_raw
                images = [img.get("src", "") for img in p_data.get("images", []) if img.get("src")]
                variants = p_data.get("variants", [])
                if variants:
                    price = str(variants[0].get("price", ""))
                    compare_price = str(variants[0].get("compare_at_price") or "")
        except Exception:
            pass

        # Fallback to schema.org HTML parser if .json failed
        if not title:
            schema_ext = SchemaOrgExtractorAdapter()
            return schema_ext.extract(url, rules)

        return build_output_result(
            title=title,
            vendor=vendor,
            price=price,
            mrp=compare_price,
            tags=tags,
            images=images,
            desc=desc,
            status="Enriched ✅",
            source_url=url,
            rules=rules,
            engine=self.engine
        )

class SchemaOrgExtractorAdapter(BaseExtractor):
    def __init__(self):
        super().__init__("generic_schema", "Generic E-commerce")
        self.engine = AmazonScraperEngine()

    def extract(self, query_or_url: str, rules: Dict[str, Any]) -> Dict[str, Any]:
        import requests
        url = query_or_url.strip()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }

        title = ""
        vendor = ""
        price = ""
        mrp = ""
        images = []
        desc = ""

        try:
            r = requests.get(url, headers=headers, timeout=15)
            html = r.text

            # Parse JSON-LD Microdata
            scripts = re.findall(r'<script[^>]*type=[\'"]application/ld\+json[\'"][^>]*>([\s\S]*?)</script>', html, re.I)
            for s in scripts:
                try:
                    data = json.loads(s.strip())
                    if isinstance(data, list):
                        data = data[0] if data else {}
                    if data.get("@type") in ["Product", "IndividualProduct"]:
                        title = data.get("name", title)
                        desc = data.get("description", desc)
                        b = data.get("brand", {})
                        vendor = b.get("name", vendor) if isinstance(b, dict) else str(b)
                        img_field = data.get("image", [])
                        if isinstance(img_field, list):
                            images.extend([i for i in img_field if isinstance(i, str)])
                        elif isinstance(img_field, str):
                            images.append(img_field)
                        offers = data.get("offers", {})
                        if isinstance(offers, list) and offers:
                            offers = offers[0]
                        if isinstance(offers, dict):
                            price = str(offers.get("price", price))
                except Exception:
                    continue

            # OpenGraph Fallback
            if not title:
                m = re.search(r'<meta\s+property=[\'"]og:title[\'"]\s+content=[\'"]([^\'"]+)[\'"]', html, re.I)
                if m: title = m.group(1)
            if not images:
                m = re.findall(r'<meta\s+property=[\'"]og:image[\'"]\s+content=[\'"]([^\'"]+)[\'"]', html, re.I)
                if m: images.extend(m)
            if not desc:
                m = re.search(r'<meta\s+property=[\'"]og:description[\'"]\s+content=[\'"]([^\'"]+)[\'"]', html, re.I)
                if m: desc = m.group(1)

        except Exception as e:
            pass

        return build_output_result(
            title=title or url,
            vendor=vendor or "Generic",
            price=price,
            mrp=mrp,
            tags=["E-Commerce"],
            images=images,
            desc=desc or f"<p>{title}</p>",
            status="Enriched ✅",
            source_url=url,
            rules=rules,
            engine=self.engine
        )

class PlatformRegistry:
    def __init__(self):
        self.adapters: Dict[str, BaseExtractor] = {
            "amazon": AmazonExtractorAdapter(),
            "shopify": ShopifyExtractorAdapter(),
            "generic_schema": SchemaOrgExtractorAdapter()
        }

    def get_supported_platforms(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": "amazon",
                "name": "Amazon",
                "subtitle": "Amazon India & Global",
                "status": "ready",
                "status_label": "Fully Supported",
                "icon": "fa-brands fa-amazon",
                "description": "High-speed extraction for product title, brand, multi-image gallery, prices, MRP, bullets, and Title Case tags with anti-bot stealth support."
            },
            {
                "id": "shopify",
                "name": "Shopify Storefronts",
                "subtitle": "Direct JSON & Catalog Feed",
                "status": "ready",
                "status_label": "Fully Supported",
                "icon": "fa-brands fa-shopify",
                "description": "Direct integration with public Shopify store endpoints for structured variant prices, images, and HTML descriptions."
            },
            {
                "id": "generic_schema",
                "name": "Generic E-commerce",
                "subtitle": "JSON-LD & Microdata Scraper",
                "status": "ready",
                "status_label": "Fully Supported",
                "icon": "fa-solid fa-code",
                "description": "Extracts schema.org and OpenGraph metadata from custom independent brand stores."
            },
            {
                "id": "flipkart",
                "name": "Flipkart",
                "subtitle": "Flipkart Marketplace",
                "status": "coming_soon",
                "status_label": "Coming Soon",
                "icon": "fa-solid fa-bag-shopping",
                "description": "Flipkart India catalog and seller API adapter currently in development roadmap."
            },
            {
                "id": "walmart",
                "name": "Walmart",
                "subtitle": "Walmart US & Global",
                "status": "coming_soon",
                "status_label": "Coming Soon",
                "icon": "fa-solid fa-store",
                "description": "Walmart marketplace extraction adapter planned for upcoming enterprise update."
            }
        ]

    get_all_platforms = get_supported_platforms

    def get_extractor(self, platform_id: str) -> BaseExtractor:
        if platform_id not in self.adapters:
            return self.adapters["amazon"]
        return self.adapters[platform_id]
