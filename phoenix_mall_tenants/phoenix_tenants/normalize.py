"""Name keys, a common category taxonomy, and date hints.

Every mall site uses its own free-text categories ("Foot Fashion, Bags, Luggage &
Accessories", "Fashion Accessiores", ...). For tenant-mix analysis we map them onto one
small taxonomy with ordered keyword rules. The raw string is always kept alongside, so
the mapping can be refined later without re-scraping.
"""
from __future__ import annotations

import csv
import html
import re
import unicodedata
from datetime import datetime
from pathlib import Path

_STOP = {"the", "store", "stores", "outlet", "boutique", "india", "pvt", "ltd", "by"}
_MALL_WORDS = re.compile(
    r"\b(phoenix|marketcity|palladium|palassio|citadel|mall of asia|indore|pune|mumbai|"
    r"bangalore|bengaluru|chennai|lucknow|ahmedabad|bareilly|kurla|wakad|hebbal)\b")


def clean_text(s) -> str:
    if s is None:
        return ""
    s = html.unescape(str(s))
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace(" ", " ")
    s = re.sub(r"\s+", " ", s).strip(" ,|-")
    return "" if s.lower() in {"none", "null", "nan"} else s


def name_key(name: str) -> str:
    """Stable key for matching the same tenant across sites/snapshots.

    'H & M' -> 'hm', 'Adidas Originals' -> 'adidas originals', "Levi's" -> 'levis',
    'adidas-indore' (slug) -> 'adidas'.
    """
    s = unicodedata.normalize("NFKD", clean_text(name)).encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " ").replace("'", "").replace("’", "")
    s = re.sub(r"[-_]+", " ", s)
    s = _MALL_WORDS.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    toks = [t for t in s.split() if t not in _STOP]
    if len(toks) > 1 and re.fullmatch(r"\d", toks[-1]):  # WordPress "montblanc-2" slugs
        toks = toks[:-1]
    # Collapse single-letter runs: "h m" -> "hm", "u s polo" -> "us polo"
    out, run = [], ""
    for t in toks:
        if len(t) == 1:
            run += t
        else:
            if run:
                out.append(run)
                run = ""
            out.append(t)
    if run:
        out.append(run)
    return " ".join(out)


def slug_to_name(slug: str) -> str:
    slug = slug.strip("/").split("/")[-1]
    slug = re.sub(r"-\d+$", "", slug)
    return re.sub(r"[-_]+", " ", slug).strip().title()


# Ordered: first match wins. Patterns run against "raw category + tenant name".
_CATEGORY_RULES: list[tuple[str, str]] = [
    ("Hypermarket & Grocery", r"hypermarket|supermarket|grocery|gourmet store|foodhall|department store"),
    ("Entertainment", r"cinema|multiplex|pvr|inox|cinepolis|gaming|arcade|timezone|smaaash|bowling|"
                      r"entertainment|trampoline|play ?area|fun ?city|snow ?world|\bvr\b"),
    ("Food & Beverage", r"food|dine|restaurant|cafe|caf[eé]|coffee|bakery|dessert|ice cream|bar\b|pub|"
                        r"brew|quick bites|qsr|beverage|cuisine|kitchen|juice|tea\b|chocolate|confection"),
    ("Watches & Jewellery", r"jewel|watch|diamond|gold"),
    ("Eyewear", r"eyewear|optic|optical|spectacle|chashme|lens"),
    ("Beauty & Personal Care", r"beauty|cosmetic|fragrance|perfume|salon|spa\b|skin|make ?up|grooming|"
                               r"personal care|wellness|pharma|chemist|nail"),
    ("Footwear", r"foot ?wear|shoe|sneaker|foot fashion"),
    ("Sports & Athleisure", r"sport|athleisure|fitness|outdoor|active ?wear"),
    ("Kids & Toys", r"\bkid|child|toy|infant|baby"),
    ("Electronics", r"electronic|mobile|gadget|digital|computer|appliance|audio|camera|telecom|\bapple\b|samsung"),
    ("Accessories", r"accessor|accessio|handbag|\bbags?\b|luggage|leather|wallet"),
    ("Home & Lifestyle", r"home|furnish|furniture|decor|kitchenware|lifestyle|gift|book|stationery|"
                         r"music|\bart\b|crafts?|houseware|mattress|\bbed|bath"),
    ("Apparel", r"fashion|apparel|cloth|wear|ethnic|denim|\b(?:wo)?mens?\b|unisex|saree|lingerie|innerwear|"
                r"couture|designer|tailor"),
    ("Services", r"service|bank|atm|forex|travel|laundry|clinic|repair|education"),
]
_CATEGORY_RX = [(c, re.compile(p, re.I)) for c, p in _CATEGORY_RULES]


def map_category(raw: str, name: str = "", tenant_type: str = "") -> str:
    if tenant_type == "dine":
        return "Food & Beverage"
    # Sites list the most specific category first ("Bags & Luggage | Foot Fashion, ..."),
    # so try each segment in order before falling back to everything plus the name.
    segments = [x for x in re.split(r"\s*\|\s*", clean_text(raw)) if x]
    for hay in segments + [f"{clean_text(raw)} {clean_text(name)}"]:
        for cat, rx in _CATEGORY_RX:
            if rx.search(hay):
                return cat
    return "Other" if raw else "Unknown"


def logo_upload_date(filename: str | None) -> str:
    """The legacy ASP.NET CMS names uploads ddMMyyyyHHmmssfff_*.ext.

    That date is when the current logo was uploaded: a lower bound for 'listed
    since' for new tenants, but a refresh for long-standing ones. Treat as a hint.
    """
    m = re.match(r"(\d{2})(\d{2})(\d{4})(\d{2})(\d{2})(\d{2})", filename or "")
    if not m:
        return ""
    try:
        d = datetime(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return ""
    return d.date().isoformat() if 2005 <= d.year <= datetime.now().year + 1 else ""


def load_tiers(path: Path | None = None) -> dict[str, str]:
    """name_key -> tier ('luxury' | 'premium' | 'bridge' | 'mass'), user-editable CSV."""
    path = path or Path(__file__).with_name("brand_tiers.csv")
    if not path.exists():
        return {}
    with path.open() as fh:
        return {name_key(r["brand"]): r["tier"].strip() for r in csv.DictReader(fh) if r.get("brand")}
