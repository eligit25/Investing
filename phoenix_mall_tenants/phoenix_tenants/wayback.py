"""Historical tenant observations from the Internet Archive's Wayback Machine.

Three complementary methods, because no single one works for every era of these sites:

1. **Listing snapshots** (`listing_history`). Fetch one archived copy per period (default
   quarterly) of each mall's directory page and extract tenant names. Works for
   server-rendered pages (WordPress, older static sites) and for archived GET JSON feeds
   (Mall of Asia's admin API). It does *not* work for pages that load their list via a
   POST/XHR after load (the current ASP.NET and MVC sites): the archive stores the empty
   shell. Those snapshots are logged as `empty_shell` so you can see the gap.

2. **Detail-page inventory** (`detail_inventory`). Every tenant has its own GET page
   (e.g. /bangalore/brand/Zara/123) that crawlers follow and archive. A CDX prefix query
   lists every such URL ever captured with timestamps and HTTP status. The first and last
   successful captures bound when a tenant was listed. This needs no HTML parsing, so it
   survives redesigns and JS-rendered listings. Caveats: crawl frequency is uneven, and
   stale detail pages can outlive a store closure.

3. **Discovery** (`discover`). Lists every archived URL on a legacy host
   (highstreetphoenix.com, phoenixcitadel.com, ...) that looks like a directory or tenant
   page, so you can add listing URLs / detail prefixes for eras the registry doesn't
   cover yet.

Raw-content URLs (`/web/<ts>id_/<url>`) are used so archived HTML comes back without the
Wayback toolbar or link rewriting. All fetched snapshots are cached on disk by digest.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

from .http import Fetcher
from .malls import Mall
from .normalize import clean_text, map_category, name_key, slug_to_name
from .platforms import _loads, _row, parse_moa, parse_wordpress_listing

CDX = "https://web.archive.org/cdx/search/cdx"
WB = "https://web.archive.org/web"
CDX_FIELDS = ("urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length")

_WB_PREFIX = re.compile(r"^(?:https?:)?(?://web\.archive\.org)?/web/\d{1,14}[a-z_]*/", re.I)
_DIRECTORY_HINT = re.compile(
    r"brand|store|shop|director|dine|dining|restaurant|food|tenant|retail|outlet|eat|cafe|leasing|"
    r"mall-?map|floor", re.I)


@dataclass
class Capture:
    timestamp: str
    original: str
    mimetype: str
    statuscode: str
    digest: str

    @property
    def date(self) -> str:
        t = self.timestamp
        return f"{t[0:4]}-{t[4:6]}-{t[6:8]}"


# ── CDX ─────────────────────────────────────────────────────────────────────────────

def cdx_query(f: Fetcher, url: str, *, match: str = "exact", from_: str | None = None,
              to: str | None = None, filters: Iterable[str] = (), collapse: str | None = None,
              fields: Iterable[str] = CDX_FIELDS, page_size: int = 5000,
              max_rows: int = 200_000) -> list[dict]:
    """Query the CDX server, following resume keys. Returns dict rows keyed by `fields`."""
    fields = tuple(fields)
    base: list[tuple[str, str]] = [("url", url), ("matchType", match), ("output", "json"),
                                   ("fl", ",".join(fields)), ("limit", str(page_size)),
                                   ("showResumeKey", "true")]
    if from_:
        base.append(("from", from_))
    if to:
        base.append(("to", to))
    if collapse:
        base.append(("collapse", collapse))
    base += [("filter", flt) for flt in filters]

    out: list[dict] = []
    resume = None
    while True:
        params = base + ([("resumeKey", resume)] if resume else [])
        r = f.get(CDX, params=params)
        r.raise_for_status()
        data = r.json() if r.text.strip() else []
        if not data:
            break
        body = data[1:] if data and data[0] == list(fields) else data
        resume = None
        # With showResumeKey the payload ends with [] and then [resumeKey].
        if len(body) >= 2 and body[-2] == [] and len(body[-1]) == 1:
            resume = body[-1][0]
            body = body[:-2]
        out += [dict(zip(fields, row)) for row in body if len(row) == len(fields)]
        if not resume or len(out) >= max_rows:
            break
    return out


def _period(ts: str, freq: str) -> str:
    y, m = ts[:4], int(ts[4:6])
    return {"Y": y, "Q": f"{y}Q{(m - 1) // 3 + 1}", "M": f"{y}-{m:02d}"}[freq]


def pick_periodic(rows: list[dict], freq: str = "Q") -> list[Capture]:
    """One capture per period: the latest successful one (closest to period end)."""
    best: dict[str, dict] = {}
    for r in rows:
        if r.get("statuscode") not in ("200", "-") and r.get("statuscode"):
            continue
        k = _period(r["timestamp"], freq)
        if k not in best or r["timestamp"] > best[k]["timestamp"]:
            best[k] = r
    return [Capture(r["timestamp"], r["original"], r.get("mimetype", ""), r.get("statuscode", ""),
                    r.get("digest", "")) for _, r in sorted(best.items())]


def fetch_snapshot(f: Fetcher, cap: Capture, cache_dir: Path) -> bytes:
    key = cap.digest or hashlib.sha1(f"{cap.timestamp}{cap.original}".encode()).hexdigest()
    path = cache_dir / f"{key}.bin"
    if path.exists():
        return path.read_bytes()
    r = f.get(f"{WB}/{cap.timestamp}id_/{cap.original}")
    r.raise_for_status()
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    return r.content


# ── Parsing archived listings ───────────────────────────────────────────────────────

def _strip_wb(href: str) -> str:
    return _WB_PREFIX.sub("", href or "")


def _norm_url(u: str) -> str:
    """'http://www.x.com:80/a/B%20C?q#f' -> 'x.com/a/B C' (archive URLs often carry :80)."""
    u = re.sub(r"^(?:https?:)?//", "", unquote(u.strip()), flags=re.I)
    u = re.sub(r"^www\.", "", u, flags=re.I)
    u = re.sub(r"^([^/]+):(?:80|443)(?=/|$)", r"\1", u)
    return u.split("#")[0].split("?")[0]


def _slug_after(path: str, prefixes: Iterable[str]) -> str:
    """First path segment after whichever detail prefix `path` starts with ('' if none)."""
    low = path.lower()
    for p in prefixes:
        pn = _norm_url(p).lower()
        if low.startswith(pn):
            return path[len(pn):].strip("/").split("/")[0]
    return ""


def _tenant_type_for(url: str, default: str = "retail") -> str:
    u = url.lower()
    if re.search(r"/(dines?|dining|restaurants?|food-?court|eat)(/|$)|api/dine", u):
        return "dine"
    if "luxury" in u:
        return "luxury"
    return default


def extract_from_links(html_text: str, mall: Mall, as_of: str, page_url: str,
                       extra_prefixes: Iterable[str] = ()) -> list[dict]:
    """Generic extractor: any anchor pointing at a tenant detail page is a tenant.

    Robust across redesigns because a directory page, whatever its markup, links each
    tenant to its detail page.
    """
    prefixes = tuple(mall.detail_prefixes) + tuple(extra_prefixes)
    if not prefixes:
        return []
    rx = Mall(mall.mall_id, mall.name, mall.city, mall.platform, mall.root,
              detail_prefixes=prefixes).detail_regex()
    soup = BeautifulSoup(html_text, "lxml")
    page_type = _tenant_type_for(page_url)
    rows, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = _strip_wb(a["href"])
        if href.startswith("/") and not href.startswith("//"):
            host = urlsplit(page_url if "://" in page_url else "http://" + page_url).netloc
            href = host + href
        path = _norm_url(href)
        if not rx.search(path):
            continue
        slug = _slug_after(path, prefixes)
        if not slug:
            continue
        img = a.find("img")
        text = clean_text(a.get_text(" ", strip=True)) or clean_text(a.get("title")) or \
            (clean_text(img.get("alt")) if img else "")
        # Skip call-to-action anchors ("View Store", "Know more") in favour of the slug.
        if not text or re.fullmatch(r"(view|know|read|explore|more|details?|visit)\b.*", text, re.I) or len(text) > 60:
            text = slug_to_name(slug)
        k = (_tenant_type_for(href, page_type), name_key(text))
        if k in seen or not k[1]:
            continue
        seen.add(k)
        rows.append(_row(mall, k[0], text, source_id=slug, detail_url="http://" + path,
                         as_of=as_of, source="wayback:listing"))
    return rows


def parse_archived(content: bytes, cap: Capture, mall: Mall, extra_prefixes=()) -> list[dict]:
    text = content.decode("utf-8", errors="replace")
    as_of = cap.date
    stripped = text.lstrip()
    tenant_type = _tenant_type_for(cap.original)
    # JSON feeds (Mall of Asia admin API, or any captured XHR)
    if "json" in cap.mimetype or stripped[:1] in "[{":
        try:
            obj = _loads(stripped)
        except ValueError:
            obj = None
        if isinstance(obj, list):
            return parse_moa(obj, mall, tenant_type, as_of, "wayback:json")
        if isinstance(obj, dict):
            items = obj.get("Brands") or obj.get("brand") or obj.get("Dines") or obj.get("dines") or []
            rows = []
            for x in items:
                nm = x.get("BrandName") or x.get("brandName") or x.get("DineName") or x.get("dineName")
                rows.append(_row(mall, "dine" if ("DineName" in x or "dineName" in x) else "retail", nm,
                                 category_raw=x.get("Categories") or x.get("brandCategory") or "",
                                 floor=x.get("BrandOnFloor") or x.get("brandFloor") or x.get("DineFloor") or "",
                                 source_id=x.get("BrandID") or x.get("id") or x.get("DineID"),
                                 as_of=as_of, source="wayback:json"))
            return rows
        return []
    if mall.platform == "wordpress":
        rows = parse_wordpress_listing(text, mall, tenant_type, as_of, "wayback:listing", cap.original)
        if rows:
            return rows
    return extract_from_links(text, mall, as_of, cap.original, extra_prefixes)


def listing_history(f: Fetcher, mall: Mall, cache_dir: Path, *, freq: str = "Q",
                    from_: str | None = None, to: str | None = None,
                    extra_urls: Iterable[str] = (), extra_prefixes: Iterable[str] = (),
                    log=print) -> tuple[list[dict], list[dict]]:
    """Returns (tenant rows, snapshot log rows)."""
    rows: list[dict] = []
    snaplog: list[dict] = []
    parsed_by_digest: dict[str, list[dict]] = {}
    for url in tuple(mall.wayback_listings) + tuple(extra_urls):
        caps = pick_periodic(cdx_query(f, url, match="exact", from_=from_, to=to,
                                       filters=("statuscode:200",)), freq)
        log(f"  {url}: {len(caps)} {freq}-periods with captures")
        for cap in caps:
            if cap.digest in parsed_by_digest:   # identical content to an earlier period
                got = [dict(r, as_of=cap.date) for r in parsed_by_digest[cap.digest]]
                status = "ok:unchanged" if got else "empty_shell"
            else:
                try:
                    content = fetch_snapshot(f, cap, cache_dir)
                except Exception as e:  # keep going; record the failure
                    snaplog.append({"mall_id": mall.mall_id, "url": url, "timestamp": cap.timestamp,
                                    "status": f"fetch_error:{type(e).__name__}", "n_tenants": 0})
                    continue
                got = parse_archived(content, cap, mall, extra_prefixes)
                parsed_by_digest[cap.digest] = got
                status = "ok" if got else "empty_shell"
            for r in got:
                r["snapshot_ts"] = cap.timestamp
                r["snapshot_url"] = f"{WB}/{cap.timestamp}/{cap.original}"
            rows += got
            snaplog.append({"mall_id": mall.mall_id, "url": url, "timestamp": cap.timestamp,
                            "status": status, "n_tenants": len(got)})
    return rows, snaplog


# ── Detail-page inventory ───────────────────────────────────────────────────────────

def detail_inventory(f: Fetcher, mall: Mall, *, from_: str | None = None, to: str | None = None,
                     extra_prefixes: Iterable[str] = (), log=print) -> list[dict]:
    """One row per tenant detail page: first/last successful capture, last status seen."""
    agg: dict[tuple, dict] = {}
    for prefix in tuple(mall.detail_prefixes) + tuple(extra_prefixes):
        caps = cdx_query(f, prefix, match="prefix", from_=from_, to=to,
                         fields=("timestamp", "original", "statuscode", "mimetype"),
                         filters=("!mimetype:image/.*", "!mimetype:text/css",
                                  "!mimetype:application/javascript"))
        log(f"  {prefix}: {len(caps)} captures")
        for c in caps:
            path = _norm_url(c["original"])
            slug = _slug_after(path, (prefix,))
            if not slug or re.search(r"\.(png|jpe?g|gif|svg|webp|css|js|pdf)$", slug, re.I):
                continue
            ttype = _tenant_type_for("/" + prefix)
            nm = slug_to_name(slug)
            k = (ttype, name_key(nm))
            a = agg.setdefault(k, {
                "mall_id": mall.mall_id, "mall_name": mall.name, "tenant_type": ttype, "name": nm,
                "name_key": k[1], "category": map_category("", nm, ttype), "first_seen": "",
                "last_seen": "", "n_ok_captures": 0, "last_capture": "", "last_status": "",
                "urls": set()})
            a["urls"].add(path)
            ts = c["timestamp"]
            if c["statuscode"] == "200":
                a["n_ok_captures"] += 1
                a["first_seen"] = min(a["first_seen"] or ts, ts)
                a["last_seen"] = max(a["last_seen"], ts)
            if ts >= a["last_capture"]:
                a["last_capture"], a["last_status"] = ts, c["statuscode"]
    out = []
    for a in agg.values():
        if not a["first_seen"]:
            continue
        a["n_urls"] = len(a.pop("urls"))
        for k in ("first_seen", "last_seen", "last_capture"):
            t = a[k]
            a[k] = f"{t[0:4]}-{t[4:6]}-{t[6:8]}" if t else ""
        # A tenant whose page now 404s/redirects has very likely left.
        a["likely_exited"] = a["last_status"] in ("404", "410", "301", "302")
        out.append(a)
    return sorted(out, key=lambda r: (r["tenant_type"], r["name_key"]))


# ── Discovery on legacy hosts ───────────────────────────────────────────────────────

def discover(f: Fetcher, host_or_prefix: str, *, log=print) -> list[dict]:
    """Every distinct archived URL under a host/prefix that looks directory-related."""
    rows = cdx_query(f, host_or_prefix, match="prefix", collapse="urlkey",
                     fields=("urlkey", "timestamp", "original", "mimetype", "statuscode"),
                     filters=("statuscode:200", "mimetype:text/html"))
    log(f"  {host_or_prefix}: {len(rows)} distinct archived HTML URLs")
    out = []
    for r in rows:
        if _DIRECTORY_HINT.search(urlsplit(r["original"]).path or ""):
            out.append({"host": host_or_prefix, "url": r["original"], "first_capture": r["timestamp"][:8]})
    # Summarise path patterns so detail prefixes are easy to spot.
    patterns: dict[str, int] = defaultdict(int)
    for r in out:
        p = urlsplit(r["url"]).path.strip("/").split("/")
        patterns["/".join(p[:-1]) + "/*" if len(p) > 1 else p[0]] += 1
    for r in out:
        p = urlsplit(r["url"]).path.strip("/").split("/")
        r["pattern"] = "/".join(p[:-1]) + "/*" if len(p) > 1 else p[0]
        r["pattern_count"] = patterns[r["pattern"]]
    return sorted(out, key=lambda r: (-r["pattern_count"], r["pattern"], r["url"]))
