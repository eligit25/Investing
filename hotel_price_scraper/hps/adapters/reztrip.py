"""RezTrip booking engine (Lemon Tree Hotels: Lemon Tree, Aurika, Keys, Red Fox).

The rooms page at reservations.lemontreehotels.com loads `/availableRooms`,
which lists every room type with its cheapest bookable rate for the dates.
RezTrip returns one (lowest) rate per room rather than every rate plan, plus
the undiscounted price when a promotion is applied.

hotels.yaml params:  portal (booking domain), code (property code, e.g. BOMAUR).
Find codes with:  python -m hps discover lemontree https://www.lemontreehotels.com/mumbai-hotels
"""
from __future__ import annotations

import re
from urllib.parse import urlencode

from ..config import Hotel
from ..dates import Stay
from ..models import NO_AVAILABILITY, OK, Offer, ScrapeResult
from .base import Adapter

ENDPOINT = "/availableRooms"
DEFAULT_PORTAL = "reservations.lemontreehotels.com"


class ReztripAdapter(Adapter):
    source = "reztrip"

    def url(self, hotel: Hotel, stay: Stay) -> str:
        p = hotel.params
        q = {
            "Rooms": 1, "CheckinDate": stay.checkin.isoformat(), "LOS": (stay.checkout - stay.checkin).days,
            "Adults_1": self.stay_cfg["adults"], "Children_1": 0, "locale": "en",
            "Currency": self.stay_cfg.get("currency", "INR"), "flow": "tf", "multi": "false",
            "hotel": p["code"],
        }
        return f"https://{p.get('portal', DEFAULT_PORTAL)}/rooms?{urlencode(q)}"

    def wanted(self, hotel: Hotel, stay: Stay):
        arrival = f"arrivalDate={stay.checkin.isoformat()}"
        code = f"propertyCode={hotel.params['code']}"

        def pred(resp) -> bool:
            return ENDPOINT in resp.url and arrival in resp.url and code in resp.url
        return pred

    def parse(self, hotel: Hotel, stay: Stay, payloads: list) -> ScrapeResult:
        return parse_available_rooms(payloads[-1], self.stay_cfg.get("currency", "INR"))


def _price(block: dict | None, currency: str) -> dict:
    return ((block or {}).get(currency) or {})


def parse_available_rooms(data: dict, currency: str = "INR") -> ScrapeResult:
    body = data.get("data") or {}
    offers = []
    for room in body.get("availableRooms") or []:
        bed = (room.get("bedTypes") or [{}])[0]
        disc = _price(room.get("discountedAveragePriceByCurrency"), currency)
        list_ = _price(room.get("averagePriceByCurrency"), currency)
        pre_tax = disc.get("roomPrice", list_.get("roomPrice"))
        incl = disc.get("totalPriceAfterTax", list_.get("totalPriceAfterTax"))
        lowest = room.get("lowestPricedRate") or {}
        policies = ((lowest.get("policyDetails") or {}).get("policies") or [{}])
        cancel = " ".join(str(p.get(k, "")) for p in policies for k in ("cancellationPolicy", "policyName", "policyCode"))
        if re.search(r"non[- ]?refundable|no refund", cancel, re.I):
            refundable = False
        elif re.search(r"cancel\w* .{0,40}allowed|free cancel|cancellationsamendments", cancel, re.I):
            refundable = True
        else:
            refundable = None
        desc = lowest.get("ratePlanLongDescription") or ""
        rate_name = lowest.get("ratePlanName") or room.get("dprTagLine") or ""
        list_price = list_.get("roomPrice")
        offers.append(Offer(
            room_code=room.get("code", ""),
            room_name=room.get("name", ""),
            rate_code=bed.get("rateCode", ""),
            rate_name=rate_name,
            price_pre_tax=float(pre_tax) if pre_tax is not None else None,
            price_incl_tax=float(incl) if incl is not None else None,
            price_list=float(list_price) if list_price and list_price != pre_tax else None,
            currency=currency,
            is_member=bool(bed.get("isLoyaltyRate")) or bool(re.search(r"\bmember", rate_name, re.I)),
            is_refundable=refundable,
            breakfast_included=True if re.search(r"breakfast", f"{rate_name} {desc}", re.I) else None,
            rooms_left=bed.get("availableRooms") if bed.get("displayAvailableRooms") else None,
            extra={"promo_text": desc[:200]},
        ))
    if not offers:
        return ScrapeResult(NO_AVAILABILITY)
    return ScrapeResult(OK, offers=offers)
