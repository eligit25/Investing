"""Live scrapers: one adapter per site platform, all returning normalized tenant rows."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Callable, Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .http import Fetcher
from .malls import Mall
from .normalize import clean_text, logo_upload_date, map_category, name_key, slug_to_name

FIELDS = ["mall_id", "mall_name", "city", "tenant_type", "name", "name_key", "category_raw",
          "category", "floor", "source_id", "detail_url", "listed_since_hint", "platform",
          "as_of", "source"]

# Fields some endpoints return that must never be stored (the legacy dine API exposes
# per-outlet login credentials fields).
_NEVER_KEEP = {"DineLoginEmail", "DineLoginPassword", "email", "password"}


def _row(mall: Mall, tenant_type: str, name: str, *, category_raw="", floor="", source_id="",
         detail_url="", listed_since_hint="", as_of: str, source: str) -> dict:
    name = clean_text(name)
    return {
        "mall_id": mall.mall_id, "mall_name": mall.name, "city": mall.city,
        "tenant_type": tenant_type, "name": name, "name_key": name_key(name),
        "category_raw": clean_text(category_raw),
        "category": map_category(category_raw, name, tenant_type),
        "floor": clean_text(floor), "source_id": str(source_id or ""), "detail_url": detail_url,
        "listed_since_hint": listed_since_hint, "platform": mall.platform, "as_of": as_of,
        "source": source,
    }


def _loads(text: str):
    """Parse JSON that may be double-encoded ("{\\"brand\\":...}") or wrapped in {"d": "..."}."""
    if not text.strip():
        return None
    obj = json.loads(text)
    for _ in range(2):
        if isinstance(obj, str):
            obj = json.loads(obj) if obj.strip() else None
        if isinstance(obj, dict) and set(obj) == {"d"}:
            obj = obj["d"]
    return obj


def _paginate(fetch_page: Callable[[int], list], id_of: Callable[[dict], str], max_pages=100) -> list:
    """Request pages by offset until a page adds nothing new.

    Handles all observed behaviours: offset paging, endpoints that return everything at
    offset 0 and nothing after, and endpoints that ignore the offset entirely.
    """
    seen: dict[str, dict] = {}
    for _ in range(max_pages):
        items = fetch_page(len(seen)) or []
        new = [i for i in items if id_of(i) not in seen]
        if not new:
            break
        for i in new:
            seen[id_of(i)] = i
    return list(seen.values())


# ── aspnet ──────────────────────────────────────────────────────────────────────────

def _aspnet_post(f: Fetcher, url: str, body: dict, referer: str) -> dict | None:
    body = dict(body)
    for _ in range(6):
        r = f.post(url, json=body, headers={"Referer": referer, "X-Requested-With": "XMLHttpRequest",
                                            "Content-Type": "application/json; charset=utf-8"})
        # Some deployments add filter params (e.g. Palladium Mumbai's 'Building').
        m = re.search(r"missing value for parameter: \\?u0027(\w+)\\?u0027|missing value for parameter: '(\w+)'",
                      r.text)
        if r.status_code == 500 and m:
            body[m.group(1) or m.group(2)] = ""
            continue
        r.raise_for_status()
        return _loads(r.text)
    raise RuntimeError(f"Could not satisfy required parameters for {url}")


def scrape_aspnet(f: Fetcher, mall: Mall, as_of: str) -> list[dict]:
    rows = []
    # GET the listing page first: it sets the session cookie that selects the mall.
    brands_page = f.get(mall.base + "/brands").url
    body = {"Count": 0, "Category": "", "SubCategory": "", "Floor": "", "Alphabet": "",
            "Order": "", "TBrandName": ""}
    if mall.extra.get("needs_building"):
        body["Building"] = ""
    brands = _paginate(
        lambda n: (_aspnet_post(f, mall.root + "/Brands.aspx/GetFilteredBrands", {**body, "Count": n},
                                brands_page) or {}).get("Brands"),
        lambda b: str(b.get("BrandID")))
    for b in brands:
        rows.append(_row(
            mall, "retail", b.get("BrandName"), category_raw=b.get("Categories"),
            floor=b.get("BrandOnFloor"), source_id=b.get("BrandID"),
            detail_url=f"{mall.base}/brand/{b.get('BrandNameURL')}/{b.get('BrandID')}",
            listed_since_hint=logo_upload_date(b.get("BrandLogo")), as_of=as_of, source="live:api"))

    dines_page = None
    for p in ("/dines", "/dine"):
        r = f.get(mall.base + p, ok_statuses=(200, 404))
        if r.status_code == 200 and "DineListing" in r.text:
            dines_page = r.url
            break
    if dines_page:
        dbody = {"Count": 0, "Category": "", "Cuisines": "", "Place": "", "Floor": "", "Alphabet": ""}
        if mall.extra.get("needs_building"):
            dbody["Building"] = ""
        dines = _paginate(
            lambda n: (_aspnet_post(f, mall.root + "/DineListing.aspx/GetFilteredDines", {**dbody, "Count": n},
                                    dines_page) or {}).get("Dines"),
            lambda d: str(d.get("DineID")))
        for d in dines:
            d = {k: v for k, v in d.items() if k not in _NEVER_KEEP}
            rows.append(_row(
                mall, "dine", d.get("DineName"), category_raw=d.get("DinePlace"), floor=d.get("DineFloor"),
                source_id=d.get("DineID"), detail_url=f"{mall.base}/dine/{d.get('DineNameURL')}",
                listed_since_hint=logo_upload_date(d.get("DinePicture")), as_of=as_of, source="live:api"))
    return rows


# ── website_mvc ─────────────────────────────────────────────────────────────────────

def scrape_website_mvc(f: Fetcher, mall: Mall, as_of: str) -> list[dict]:
    rows = []
    for tenant_type, page, endpoint, key, body in [
        ("retail", "/brands", "/Website/Brands/GetFilteredBrands", "brand",
         {"Category": "", "SubCategory": "", "Floor": "", "Alphabet": "", "Order": "", "TBrandName": ""}),
        ("dine", "/dines", "/Website/Dines/GetFilteredDines", "dines",
         {"Category": "", "Cuisine": "", "Place": "", "Floor": "", "Alphabet": "", "Order": "", "TBrandName": ""}),
    ]:
        referer = f.get(mall.base + page).url

        def page_fn(n, endpoint=endpoint, body=body, key=key, referer=referer):
            r = f.post(mall.base + endpoint, data={"Count": n, **body},
                       headers={"Referer": referer, "X-Requested-With": "XMLHttpRequest"})
            r.raise_for_status()
            return (_loads(r.text) or {}).get(key)

        p = "dine" if tenant_type == "dine" else "brand"
        for x in _paginate(page_fn, lambda x: str(x.get("id"))):
            slug = x.get(f"{p}Slug") or ""
            rows.append(_row(
                mall, tenant_type, x.get(f"{p}Name"),
                category_raw=x.get(f"{p}Category") or x.get(f"{p}Cuisine") or x.get(f"{p}Place"),
                floor=x.get(f"{p}Floor"), source_id=x.get("id"),
                detail_url=urljoin(mall.base + "/", slug) if slug else "", as_of=as_of, source="live:api"))
    return rows


# ── citadel_mvc ─────────────────────────────────────────────────────────────────────

def scrape_citadel_mvc(f: Fetcher, mall: Mall, as_of: str) -> list[dict]:
    rows = []
    for tenant_type, page, type_id in [("retail", "/brands", mall.extra["retail_type_id"]),
                                       ("dine", "/dines", mall.extra["dine_type_id"])]:
        referer = f.get(mall.base + page).url
        r = f.post(mall.base + "/Brands/GetBrandData",
                   data={"flag": "ALL", "ProductTypeID": type_id, "foodfestId": 0},
                   headers={"Referer": referer, "X-Requested-With": "XMLHttpRequest"})
        r.raise_for_status()
        for x in r.json() or []:
            if str(x.get("isPublished")).lower() == "false":
                continue
            rows.append(_row(
                mall, tenant_type, x.get("title"),
                category_raw=", ".join(v for v in (x.get("category"), x.get("place")) if v and v != "None"),
                floor=x.get("floor"), source_id=x.get("id"),
                detail_url=f"{mall.base}{page}/{x.get('seourl')}",
                listed_since_hint=(x.get("createdOn") or "")[:10], as_of=as_of, source="live:api"))
    return rows


# ── moa_json ────────────────────────────────────────────────────────────────────────

def parse_moa(items: Iterable[dict], mall: Mall, tenant_type: str, as_of: str, source: str) -> list[dict]:
    seg = {"retail": "brand", "dine": "dine", "luxury": "luxury"}[tenant_type]
    return [_row(mall, tenant_type, x.get("title"), category_raw=x.get("category"), floor=x.get("floor"),
                 source_id=x.get("id"), detail_url=f"{mall.root}/{seg}/{clean_text(x.get('title'))}",
                 as_of=as_of, source=source)
            for x in items if isinstance(x, dict) and x.get("title")]


def scrape_moa_json(f: Fetcher, mall: Mall, as_of: str) -> list[dict]:
    api = mall.extra["api"]
    rows: list[dict] = []
    for ep, tenant_type in (("brandluxury", "luxury"), ("brand", "retail"), ("dine", "dine")):
        r = f.get(f"{api}/{ep}", headers={"Referer": mall.root + "/"})
        r.raise_for_status()
        rows += parse_moa(r.json(), mall, tenant_type, as_of, "live:api")
    # A luxury tenant can also appear in the main brand feed; keep the luxury row.
    seen, out = set(), []
    for x in rows:
        if x["name_key"] in seen:
            continue
        seen.add(x["name_key"])
        out.append(x)
    return out


# ── wordpress (Palladium Chennai) ───────────────────────────────────────────────────

def parse_wordpress_listing(html_text: str, mall: Mall, tenant_type: str, as_of: str, source: str,
                            page_url: str) -> list[dict]:
    """Store tiles are image links to https://palladiumchennai.com/<slug>/. Names come from slugs."""
    soup = BeautifulSoup(html_text, "lxml")
    host = re.sub(r"^https?://(www\.)?", "", mall.root)
    skip = re.compile(r"/(shop-by|dine|site|wp-|category|tag|blog|event|offer|contact|about|newly|"
                      r"careers?|privacy|terms|page)\b", re.I)
    rows, seen = [], set()
    scope = soup.select(".vc_tta-panel") or [soup]
    for panel in scope:
        for fig in panel.select("figure a[href], .wpb_single_image a[href]"):
            href = fig["href"]
            # Wayback rewrites links to /web/<ts>/<original>; strip that prefix.
            href = re.sub(r"^(https?://web\.archive\.org)?/web/\d+[a-z_]*/", "", href)
            if host not in href or skip.search(href):
                continue
            slug = href.rstrip("/").split("/")[-1]
            if not slug or slug in seen or slug == host:
                continue
            seen.add(slug)
            img = fig.find("img")
            name = (img.get("alt") or "").strip() if img else ""
            rows.append(_row(mall, tenant_type, name or slug_to_name(slug), source_id=slug,
                             detail_url=href, as_of=as_of, source=source))
    return rows


def wordpress_categories(html_text: str) -> dict[str, str]:
    """slug -> category from /shop-by-category/, whose tabs are titled CLOTHING, BAGS, ..."""
    soup = BeautifulSoup(html_text, "lxml")
    out: dict[str, str] = {}
    for panel in soup.select(".vc_tta-panel"):
        title = panel.select_one(".vc_tta-title-text")
        if not title:
            continue
        for a in panel.select("figure a[href]"):
            slug = a["href"].rstrip("/").split("/")[-1]
            out.setdefault(slug, title.get_text(strip=True).title())
    return out


def scrape_wordpress(f: Fetcher, mall: Mall, as_of: str) -> list[dict]:
    rows = []
    # Dine first: restaurant tiles are repeated on the store page and the dine label should win.
    for page, tenant_type in (("/dine-all/", "dine"), ("/shop-by-store/", "retail")):
        r = f.get(mall.root + page, ok_statuses=(200, 404))
        if r.status_code == 200:
            rows += parse_wordpress_listing(r.text, mall, tenant_type, as_of, "live:html", r.url)
    r = f.get(mall.root + "/shop-by-category/", ok_statuses=(200, 404))
    cats = wordpress_categories(r.text) if r.status_code == 200 else {}
    seen, out = set(), []
    for x in rows:
        if x["source_id"] in seen:
            continue
        seen.add(x["source_id"])
        if cats.get(x["source_id"]):
            x["category_raw"] = cats[x["source_id"]]
            x["category"] = map_category(x["category_raw"], x["name"], x["tenant_type"])
        out.append(x)
    return out


SCRAPERS = {
    "aspnet": scrape_aspnet,
    "website_mvc": scrape_website_mvc,
    "citadel_mvc": scrape_citadel_mvc,
    "moa_json": scrape_moa_json,
    "wordpress": scrape_wordpress,
}


def scrape_current(f: Fetcher, mall: Mall, as_of: str | None = None) -> list[dict]:
    as_of = as_of or datetime.now(timezone.utc).date().isoformat()
    return SCRAPERS[mall.platform](f, mall, as_of)
