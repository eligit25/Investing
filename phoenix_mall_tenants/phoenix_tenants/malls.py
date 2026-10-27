"""Registry of Phoenix Mills retail assets and how each one's store directory is served.

Each mall's website runs on one of five back-ends (verified Oct 2026):

    aspnet       Legacy ASP.NET WebForms platform. POST {root}/Brands.aspx/GetFilteredBrands
                 and /DineListing.aspx/GetFilteredDines. The mall is chosen by a session
                 cookie set when you GET the mall's /brands page. An F5 WAF rejects the
                 POST unless a Referer header is sent.
    website_mvc  Newer .NET MVC sites. POST {root}/Website/Brands/GetFilteredBrands
                 (form-encoded, paged by "Count" offset). Response is JSON, sometimes
                 double-encoded as a JSON string.
    citadel_mvc  Indore site. POST /Brands/GetBrandData with ProductTypeID 1 (retail)
                 or 68 (dine). Records carry a `createdOn` timestamp.
    moa_json     Mall of Asia React app backed by open GET JSON endpoints on
                 admin.phoenixmallofasia.com.
    wordpress    Palladium Chennai. Server-rendered WPBakery page; store names are only
                 available from the link slugs.

`wayback_listings` and `detail_prefixes` include legacy domains and paths the malls used
before their current sites (seen in Wayback availability data), so history can reach back
to ~2013 for the older assets.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Mall:
    mall_id: str
    name: str
    city: str
    platform: str
    root: str                      # scheme + host, no trailing slash
    path: str = ""                 # mall sub-path on shared hosts, e.g. "/bangalore"
    # Archived pages that list tenants (current + legacy). Used by the Wayback listing pass.
    wayback_listings: tuple[str, ...] = ()
    # URL prefixes of per-tenant detail pages (current + legacy). Used by the Wayback
    # detail-page inventory pass; matched with CDX matchType=prefix.
    detail_prefixes: tuple[str, ...] = ()
    # Hosts worth running `discover` on to find legacy directory URLs.
    legacy_hosts: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict)

    @property
    def base(self) -> str:
        return self.root + self.path

    def detail_regex(self) -> re.Pattern:
        """Regex that matches detail-page URLs for this mall (any scheme / www)."""
        parts = []
        for p in self.detail_prefixes:
            p = re.sub(r"^https?://(www\.)?", "", p)
            parts.append(re.escape(p) + r"[^\s\"'?#<>]+")
        return re.compile(r"(?:https?://)?(?:www\.)?(?:" + "|".join(parts) + ")", re.I)


PMC = "https://www.phoenixmarketcity.com"

MALLS: dict[str, Mall] = {m.mall_id: m for m in [
    Mall(
        "pmc_bangalore", "Phoenix Marketcity Bangalore", "Bangalore", "aspnet", PMC, "/bangalore",
        wayback_listings=("phoenixmarketcity.com/bangalore/brands", "phoenixmarketcity.com/bangalore/dines",
                          "phoenixmarketcity.com/bangalore/dines/Restaurant",
                          "phoenixmarketcity.com/bangalore/dines/Food-Court"),
        detail_prefixes=("phoenixmarketcity.com/bangalore/brand/", "phoenixmarketcity.com/bangalore/dine/"),
        legacy_hosts=("phoenixmarketcity.com/bangalore",),
    ),
    Mall(
        "pmc_chennai", "Phoenix Marketcity Chennai", "Chennai", "aspnet", PMC, "/chennai",
        wayback_listings=("phoenixmarketcity.com/chennai/brands", "phoenixmarketcity.com/chennai/dines",
                          "phoenixmarketcity.com/chennai/dines/Restaurant",
                          "phoenixmarketcity.com/chennai/dines/Food-Court"),
        detail_prefixes=("phoenixmarketcity.com/chennai/brand/", "phoenixmarketcity.com/chennai/dine/"),
        legacy_hosts=("phoenixmarketcity.com/chennai",),
    ),
    Mall(
        "palladium_mumbai", "Palladium Mumbai (Lower Parel)", "Mumbai", "aspnet", "https://phoenixpalladium.com",
        wayback_listings=("phoenixpalladium.com/brands", "phoenixpalladium.com/dine"),
        detail_prefixes=("phoenixpalladium.com/brand/", "phoenixpalladium.com/dine/"),
        # Pre-2021 the Lower Parel complex was marketed as High Street Phoenix / Palladium Mumbai.
        # Their directory URLs are unknown until `discover` is run against these hosts.
        legacy_hosts=("highstreetphoenix.com", "palladiummumbai.com"),
        extra={"needs_building": True},
    ),
    Mall(
        "palladium_ahmedabad", "Palladium Ahmedabad", "Ahmedabad", "aspnet", "https://www.palladiumahmedabad.com",
        wayback_listings=("palladiumahmedabad.com/brands", "palladiumahmedabad.com/dine"),
        detail_prefixes=("palladiumahmedabad.com/brand/", "palladiumahmedabad.com/dine/"),
    ),
    Mall(
        "mall_of_millennium_pune", "Phoenix Mall of the Millennium (Wakad)", "Pune", "aspnet",
        "https://www.mallofthemillennium.com",
        wayback_listings=("mallofthemillennium.com/brands", "mallofthemillennium.com/dine"),
        detail_prefixes=("mallofthemillennium.com/brand/", "mallofthemillennium.com/dine/"),
        extra={"needs_building": True},
    ),
    Mall(
        "united_lucknow", "Phoenix United Lucknow", "Lucknow", "aspnet", "https://www.phoenixunited.in", "/lucknow",
        wayback_listings=("phoenixunited.in/lucknow/brands", "phoenixunited.in/lucknow/dines"),
        detail_prefixes=("phoenixunited.in/lucknow/brand/", "phoenixunited.in/lucknow/dine/"),
    ),
    Mall(
        "united_bareilly", "Phoenix United Bareilly", "Bareilly", "aspnet", "https://www.phoenixunited.in", "/bareilly",
        wayback_listings=("phoenixunited.in/bareilly/brands", "phoenixunited.in/bareilly/dines"),
        detail_prefixes=("phoenixunited.in/bareilly/brand/", "phoenixunited.in/bareilly/dine/"),
    ),
    Mall(
        "pmc_mumbai", "Phoenix Marketcity Mumbai (Kurla)", "Mumbai", "website_mvc",
        "https://www.phoenixmarketcitymumbai.com",
        wayback_listings=("phoenixmarketcitymumbai.com/brands", "phoenixmarketcitymumbai.com/dines",
                          "phoenixmarketcity.com/mumbai/brands", "phoenixmarketcity.com/mumbai/dines"),
        detail_prefixes=("phoenixmarketcitymumbai.com/brands/", "phoenixmarketcitymumbai.com/dines/",
                         "phoenixmarketcity.com/mumbai/brand/", "phoenixmarketcity.com/mumbai/dine/"),
        legacy_hosts=("phoenixmarketcity.com/mumbai",),
    ),
    Mall(
        "pmc_pune", "Phoenix Marketcity Pune (Avenue of Stars)", "Pune", "website_mvc", "https://phoenixavenueofstars.com",
        wayback_listings=("phoenixavenueofstars.com/brands", "phoenixavenueofstars.com/dines",
                          "phoenixmarketcity.com/pune/brands", "phoenixmarketcity.com/pune/dines"),
        detail_prefixes=("phoenixavenueofstars.com/brands/", "phoenixavenueofstars.com/dines/",
                         "phoenixmarketcity.com/pune/brand/", "phoenixmarketcity.com/pune/dine/"),
        # phoenixmarketcitypune.com has captures 2014-2016.
        legacy_hosts=("phoenixmarketcity.com/pune", "phoenixmarketcitypune.com"),
    ),
    Mall(
        "palassio_lucknow", "Phoenix Palassio", "Lucknow", "website_mvc", "https://www.phoenixpalassio.com",
        wayback_listings=("phoenixpalassio.com/brands", "phoenixpalassio.com/dines"),
        detail_prefixes=("phoenixpalassio.com/brands/", "phoenixpalassio.com/dines/",
                         "phoenixpalassio.com/brand/", "phoenixpalassio.com/dine/"),
    ),
    Mall(
        "citadel_indore", "Phoenix Citadel / Pride of India", "Indore", "citadel_mvc", "https://phoenixprideofindia.com",
        wayback_listings=("phoenixprideofindia.com/brands", "phoenixprideofindia.com/dines",
                          "phoenixcitadel.com/brands", "phoenixcitadel.com/dines"),
        detail_prefixes=("phoenixprideofindia.com/brands/", "phoenixprideofindia.com/dines/",
                         "phoenixcitadel.com/brands/", "phoenixcitadel.com/dines/"),
        legacy_hosts=("phoenixcitadel.com",),
        extra={"retail_type_id": 1, "dine_type_id": 68},
    ),
    Mall(
        "mall_of_asia_bangalore", "Phoenix Mall of Asia (Hebbal)", "Bangalore", "moa_json",
        "https://www.phoenixmallofasia.com",
        wayback_listings=("admin.phoenixmallofasia.com/api/brand", "admin.phoenixmallofasia.com/api/dine",
                          "admin.phoenixmallofasia.com/api/brandluxury"),
        detail_prefixes=("phoenixmallofasia.com/brand/", "phoenixmallofasia.com/dine/",
                         "phoenixmallofasia.com/luxury/"),
        extra={"api": "https://admin.phoenixmallofasia.com/api"},
    ),
    Mall(
        "palladium_chennai", "Palladium Chennai", "Chennai", "wordpress", "https://palladiumchennai.com",
        wayback_listings=("palladiumchennai.com/shop-by-store/", "palladiumchennai.com/dine-all/"),
        # WordPress puts stores at the site root, so there is no detail prefix to inventory.
        detail_prefixes=(),
    ),
]}


def get_malls(ids: list[str] | None = None) -> list[Mall]:
    if not ids:
        return list(MALLS.values())
    unknown = [i for i in ids if i not in MALLS]
    if unknown:
        raise SystemExit(f"Unknown mall id(s): {unknown}. Known: {sorted(MALLS)}")
    return [MALLS[i] for i in ids]
