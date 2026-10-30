"""Sabre SynXis booking engine (ITC Hotels, The Leela, Oberoi/Trident, and many others).

The hotel-level availability page loads all rooms x rates for the requested
dates from `/gw/product/v1/getProductAvailability`. One page load per check-in
date; the response includes room names, rate plans, taxes, cancellation terms
and remaining inventory.

hotels.yaml params:  base_url (the chain's SynXis domain), chain, hotel
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlencode

from ..config import Hotel
from ..dates import Stay
from ..models import NO_AVAILABILITY, OK, Offer, ScrapeResult
from .base import Adapter

ENDPOINT = "/gw/product/v1/getProductAvailability"
NON_REFUNDABLE = re.compile(r"non[- ]?refundable|no refund|prepaid|advance purchase", re.I)


class SynxisAdapter(Adapter):
    source = "synxis"

    def url(self, hotel: Hotel, stay: Stay) -> str:
        p = hotel.params
        q = {
            "adult": self.stay_cfg["adults"], "child": 0, "rooms": 1,
            "arrive": stay.checkin.isoformat(), "depart": stay.checkout.isoformat(),
            "chain": p["chain"], "hotel": p["hotel"], "level": "hotel",
            "currency": self.stay_cfg.get("currency", "INR"), "locale": "en-US",
        }
        q.update(p.get("extra_query", {}))
        return f"{p['base_url'].rstrip('/')}/?{urlencode(q)}"

    def wanted(self, hotel: Hotel, stay: Stay):
        start = stay.checkin.isoformat()

        def pred(resp) -> bool:
            if ENDPOINT not in resp.url or resp.request.method != "POST":
                return False
            # Only the response for the requested dates (ignore stale/prefetch calls).
            return start in (resp.request.post_data or "")
        return pred

    def parse(self, hotel: Hotel, stay: Stay, payloads: list) -> ScrapeResult:
        return parse_product_availability(payloads[-1], nights=(stay.checkout - stay.checkin).days)


def _cancel_refundable(policy: dict | None) -> bool | None:
    if not policy:
        return None
    text = " ".join(str(policy.get(k, "")) for k in ("Code", "Description", "CancelFeeType"))
    if NON_REFUNDABLE.search(text):
        return False
    if re.search(r"free cancel|cancel(lation)? (permitted|allowed)|without (any )?charge", text, re.I):
        return True
    if policy.get("CancelTimeIn") not in (None, "", 0):
        return True
    return None


def parse_product_availability(data: dict, nights: int = 1) -> ScrapeResult:
    if isinstance(data, str):
        data = json.loads(data)
    content = data.get("ContentLists", {}) or {}
    rooms = {r["Code"]: r for r in content.get("RoomList", []) or []}
    rates = {r["Code"]: r for r in content.get("RateList", []) or []}
    cancels = {c["Code"]: c for c in (content.get("PolicyList", {}) or {}).get("CancelPolicy", []) or []}
    bookings = {c["Code"]: c for c in (content.get("PolicyList", {}) or {}).get("BookingPolicy", []) or []}
    detail = data.get("ProductAvailabilityDetail", {}) or {}
    prices = detail.get("Prices") or []
    currency = (content.get("CurrencyList") or [{}])[0].get("CurrencyCode", "INR")

    offers = []
    for item in prices:
        prod = item.get("Product", {})
        room_code = prod.get("Room", {}).get("Code", "")
        rate_code = prod.get("Rate", {}).get("Code", "")
        room = rooms.get(room_code, {})
        rate = rates.get(rate_code, {})
        rdet = rate.get("Details", {}) or {}
        per_night = ((prod.get("Prices") or {}).get("PerNight") or {}).get("Price") or {}
        total = per_night.get("Total") or {}
        pre_tax = total.get("Amount", per_night.get("Amount"))
        incl_tax = total.get("AmountWithTaxesFees")
        if pre_tax is None:  # fall back to stay total
            stay_total = ((prod.get("Prices") or {}).get("Total") or {}).get("Price") or {}
            if stay_total.get("Amount") is not None:
                pre_tax = stay_total["Amount"] / max(nights, 1)
                t = (stay_total.get("Total") or {}).get("AmountWithTaxesFees")
                incl_tax = t / max(nights, 1) if t is not None else None

        rate_name = rate.get("Name") or rdet.get("DisplayName") or rate_code
        is_member = (prod.get("Ref") == "Loyalty"
                     or any(c.get("programID") for c in rdet.get("CustLoyaltyList", []) or [])
                     or bool(re.search(r"\bmember", rate_name, re.I)))
        refundable = _cancel_refundable(cancels.get(prod.get("CancelPolicy", {}).get("Code")))
        if NON_REFUNDABLE.search(rate_name):
            refundable = False
        booking = bookings.get(prod.get("BookingPolicy", {}).get("Code"), {})
        indicators = rdet.get("Indicators", {}) or {}
        offers.append(Offer(
            room_code=room_code,
            room_name=room.get("Name", room_code),
            rate_code=rate_code,
            rate_name=rate_name,
            price_pre_tax=float(pre_tax) if pre_tax is not None else None,
            price_incl_tax=float(incl_tax) if incl_tax is not None else None,
            currency=currency,
            is_member=is_member,
            is_refundable=refundable,
            breakfast_included=indicators.get("BreakfastIncluded"),
            # Negative inventory = room type oversold at the hotel; treat as unknown.
            rooms_left=inv if isinstance(inv := item.get("AvailableInventory"), int) and inv >= 0 else None,
            extra={"rate_class": rdet.get("RateClass"), "deposit": booking.get("RefundableStay")},
        ))

    if not offers:
        failure = detail.get("LeastRestrictiveFailure") or {}
        return ScrapeResult(NO_AVAILABILITY, error=f"{failure.get('Level', '')} {failure.get('ProductStatus', '')}".strip())
    return ScrapeResult(OK, offers=offers)
