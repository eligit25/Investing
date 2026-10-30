"""Capture adapters for chains whose sites block cloud servers (Taj, Marriott, Hyatt).

These could not be tested from a datacenter IP, so they are built to be
finished on your own computer:

1. `python -m hps test-site <source>` opens a real booking page for one hotel,
   records every JSON response, a screenshot and the HTML, and writes a probe
   report folder you can inspect or send back for a dedicated parser.
2. If the default URL template is wrong, search the hotel on the chain's site
   in your normal browser, copy the rooms page URL and run
   `python -m hps learn-url <hotel_id> "<url>" <checkin> <checkout>`
   to store it as that hotel's template.
3. Prices are extracted with a generic JSON heuristic (room-name + price
   fields) until a chain-specific parser is written from a probe sample.

URL templates use Python format fields: {code}, {adults}, {checkin:%Y-%m-%d},
{checkout:%m/%d/%Y}, {nights}.
"""
from __future__ import annotations

import re
from typing import Any

from ..config import Hotel
from ..dates import Stay
from ..models import OK, PARSE_ERROR, Offer, ScrapeResult
from .base import Adapter

# Best-known defaults. Each is overridable per hotel with params.url_template /
# params.response_patterns in hotels.yaml (learn-url writes url_template for you).
CHAIN_DEFAULTS: dict[str, dict[str, Any]] = {
    "marriott": {
        "home": "https://www.marriott.com/default.mi",
        "url_template": (
            "https://www.marriott.com/reservation/availabilitySearch.mi?propertyCode={code}&isSearch=true"
            "&fromDate={checkin:%m/%d/%Y}&toDate={checkout:%m/%d/%Y}&numberOfRooms=1&numberOfAdults={adults}"
            "&childrenCount=0&useRewardsPoints=false&flexibleDateSearch=false"
        ),
        "response_patterns": [r"/mi/query/", r"graphql", r"availab", r"rate", r"room"],
    },
    "hyatt": {
        "home": "https://www.hyatt.com/",
        "url_template": (
            "https://www.hyatt.com/shop/rooms/{code}?checkinDate={checkin:%Y-%m-%d}"
            "&checkoutDate={checkout:%Y-%m-%d}&rooms=1&adults={adults}&kids=0&rate=Standard"
        ),
        "response_patterns": [r"/shop/", r"room", r"rate", r"availab"],
    },
    "taj": {
        "home": "https://www.tajhotels.com/en-in/",
        # Taj's booking URL format is not known yet: set params.url_template with learn-url.
        "url_template": None,
        "response_patterns": [r"availab", r"rate", r"room", r"price", r"graphql", r"booking"],
    },
}

NAME_KEY = re.compile(r"^(room|roomtype|roomcategory)?(name|title|type|typename|displayname|description)$|room.?(name|title)", re.I)
PRICE_KEY = re.compile(r"price|rate|amount|total|cost|tariff", re.I)
NOT_PRICE_KEY = re.compile(r"tax|discount|saving|fee|deposit|point|count|id$|code|percent|occupan|max|min(?!.*price)|limit|size", re.I)
TAX_INCL_KEY = re.compile(r"(after|with|incl).?tax|aftertax|withtax|grand", re.I)
PRE_TAX_KEY = re.compile(r"(before|pre|excl|without).?tax|net.?rate|base.?(price|rate|amount)", re.I)


def _num(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.replace(",", "").replace("₹", "").replace("INR", "").strip()
        if re.fullmatch(r"\d+(\.\d+)?", s):
            return float(s)
    return None


def _price_fields(d: dict, depth: int = 0) -> tuple[list[float], list[float]]:
    """(pre-tax-ish prices, tax-inclusive prices) found in d and its child dicts."""
    pre, incl = [], []
    for k, v in d.items():
        if isinstance(v, dict) and depth < 2:
            a, b = _price_fields(v, depth + 1)
            if PRICE_KEY.search(k) or depth == 0:
                pre += a
                incl += b
            continue
        n = _num(v)
        if n is None or not 300 <= n <= 2_000_000:
            continue
        if TAX_INCL_KEY.search(k):
            incl.append(n)
        elif PRE_TAX_KEY.search(k) or (PRICE_KEY.search(k) and not NOT_PRICE_KEY.search(k)):
            pre.append(n)
    return pre, incl


def heuristic_offers(payloads: list[Any], currency: str = "INR") -> list[Offer]:
    """Find objects that look like a room/rate with a price, anywhere in the JSON."""
    found: dict[tuple[str, float], Offer] = {}

    def walk(o: Any, inherited_name: str = ""):
        if isinstance(o, dict):
            name = next((v.strip() for k, v in o.items()
                         if isinstance(v, str) and NAME_KEY.search(k) and 3 <= len(v.strip()) <= 120), "")
            name = name or inherited_name
            pre, incl = _price_fields(o)
            if name and (pre or incl):
                price = min(pre) if pre else None
                key = (name, price or min(incl))
                if key not in found:
                    found[key] = Offer(room_code="", room_name=name, rate_code="", rate_name="",
                                       price_pre_tax=price, price_incl_tax=min(incl) if incl else None,
                                       currency=currency, extra={"parser": "heuristic"})
            for v in o.values():
                if isinstance(v, (dict, list)):
                    walk(v, name)
        elif isinstance(o, list):
            for v in o:
                walk(v, inherited_name)

    for p in payloads:
        walk(p)
    return list(found.values())


class CaptureAdapter(Adapter):
    """Generic: open a templated booking URL, keep matching JSON, parse heuristically."""

    _warmed = False

    def warm_up(self, hotel: Hotel) -> None:
        """Visit the chain's home page once per run before any booking page, like a visitor."""
        home = self._cfg(hotel).get("home")
        if home and not self._warmed:
            self._warmed = True
            self.session.capture(home, lambda r: False, wait_for_first=False, settle_s=4)

    def _cfg(self, hotel: Hotel) -> dict:
        cfg = dict(CHAIN_DEFAULTS.get(self.source, {}))
        cfg.update({k: v for k, v in hotel.params.items() if k in ("url_template", "response_patterns", "home")})
        return cfg

    def url(self, hotel: Hotel, stay: Stay) -> str:
        tpl = self._cfg(hotel).get("url_template")
        if not tpl:
            raise ValueError(f"{hotel.id}: no url_template yet - run `python -m hps learn-url {hotel.id} \"<url>\" <checkin> <checkout>`")
        return tpl.format(code=hotel.params.get("code", ""), adults=self.stay_cfg["adults"],
                          checkin=stay.checkin, checkout=stay.checkout,
                          nights=(stay.checkout - stay.checkin).days)

    def wanted(self, hotel: Hotel, stay: Stay):
        pats = [re.compile(p, re.I) for p in self._cfg(hotel).get("response_patterns", [])]

        def pred(resp) -> bool:
            if resp.request.resource_type not in ("xhr", "fetch"):
                return False
            return any(p.search(resp.url) for p in pats)
        return pred

    def scrape(self, hotel: Hotel, stay: Stay) -> ScrapeResult:
        try:
            url = self.url(hotel, stay)
        except ValueError as e:
            return ScrapeResult(PARSE_ERROR, error=str(e))
        # Rate calls on these sites can fire late and in several parts: wait for the page to settle.
        cap = self.session.capture(url, self.wanted(hotel, stay), wait_for_first=False, settle_s=20)
        return self.result_from_capture(hotel, stay, cap)

    def parse(self, hotel: Hotel, stay: Stay, payloads: list) -> ScrapeResult:
        offers = heuristic_offers(payloads, self.stay_cfg.get("currency", "INR"))
        if not offers:
            return ScrapeResult(PARSE_ERROR, error="captured JSON but found no room/price objects (send a probe report)")
        return ScrapeResult(OK, offers=offers)


class TajAdapter(CaptureAdapter):
    source = "taj"


class MarriottAdapter(CaptureAdapter):
    source = "marriott"


class HyattAdapter(CaptureAdapter):
    source = "hyatt"
