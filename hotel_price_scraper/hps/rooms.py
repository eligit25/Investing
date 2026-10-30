"""Map raw offers onto the standard rooms (A, B) and pick one price per room.

Room A is the hotel's entry category and room B the step-up category, as
configured per hotel in hotels.yaml. Hotels without a rule fall back to the
cheapest (A) and second-cheapest (B) room categories on the day, which is
recorded as method="auto" so it can be told apart from configured mappings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Hotel
from .models import Offer

STD_ROOMS = ("A", "B")
LOWEST = "LOWEST"  # cheapest eligible offer in any room, for a headline price


@dataclass
class StdPrice:
    room_std: str              # "A", "B" or "LOWEST"
    method: str                # "config" or "auto"
    offer: Offer | None        # None -> room not on sale (sold out / closed)
    n_offers_for_room: int     # all offers seen for that room, eligible or not


def _matches(offer: Offer, codes: list[str], names: list[str]) -> bool:
    if offer.room_code and offer.room_code.upper() in {c.upper() for c in codes}:
        return True
    return any(re.search(p, offer.room_name or "", re.I) for p in names)


def is_eligible(offer: Offer, policy: dict) -> bool:
    if offer.price_pre_tax is None or offer.price_pre_tax <= 0:
        return False
    if policy.get("exclude_member_rates", True) and offer.is_member:
        return False
    # Unknown refundability (None) is allowed; only explicit non-refundable is excluded.
    if policy.get("require_refundable", True) and offer.is_refundable is False:
        return False
    return not any(re.search(p, offer.rate_name or "", re.I) for p in policy.get("exclude_rate_patterns", []))


def _excluded_room(offer: Offer, policy: dict) -> bool:
    return any(re.search(p, offer.room_name or "", re.I) for p in policy.get("exclude_room_patterns", []))


def assign_rooms(hotel: Hotel, offers: list[Offer], policy: dict) -> str:
    """Set offer.room_std in place. Returns the method used ("config" or "auto")."""
    if hotel.rooms:
        for o in offers:
            o.room_std = None
            if _excluded_room(o, policy):
                continue
            for std in STD_ROOMS:
                rule = hotel.rooms.get(std)
                if rule and _matches(o, rule.codes, rule.names):
                    o.room_std = std
                    break
        return "config"

    # Auto: rank room categories by their cheapest eligible price.
    cheapest: dict[str, float] = {}
    for o in offers:
        if _excluded_room(o, policy) or not is_eligible(o, policy):
            continue
        key = o.room_code or o.room_name
        cheapest[key] = min(cheapest.get(key, float("inf")), o.price_pre_tax)
    ranked = sorted(cheapest, key=cheapest.get)
    mapping = dict(zip(ranked, STD_ROOMS))
    for o in offers:
        o.room_std = mapping.get(o.room_code or o.room_name)
    return "auto"


def standard_prices(hotel: Hotel, offers: list[Offer], policy: dict) -> list[StdPrice]:
    method = assign_rooms(hotel, offers, policy)
    out = []
    for std in STD_ROOMS:
        in_room = [o for o in offers if o.room_std == std]
        eligible = [o for o in in_room if is_eligible(o, policy)]
        best = min(eligible, key=lambda o: o.price_pre_tax) if eligible else None
        out.append(StdPrice(std, method, best, len(in_room)))
    eligible_any = [o for o in offers if is_eligible(o, policy) and not _excluded_room(o, policy)]
    best_any = min(eligible_any, key=lambda o: o.price_pre_tax) if eligible_any else None
    out.append(StdPrice(LOWEST, method, best_any, len(offers)))
    return out
