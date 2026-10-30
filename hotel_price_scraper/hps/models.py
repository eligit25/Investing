"""Plain data records passed between adapters, room logic and storage."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Job statuses
OK = "ok"                          # prices captured
NO_AVAILABILITY = "no_availability"  # site answered, hotel has nothing to sell (sold out / closed)
BLOCKED = "blocked"                # bot protection / access denied
PARSE_ERROR = "parse_error"        # response captured but not understood
ERROR = "error"                    # timeout, navigation failure, etc.
SKIPPED = "skipped"                # not attempted (e.g. chain blocked earlier this run)


@dataclass
class Offer:
    """One room x rate combination as shown by a booking engine."""
    room_code: str
    room_name: str
    rate_code: str
    rate_name: str
    price_pre_tax: float | None       # per night, for the whole stay / nights
    price_incl_tax: float | None
    currency: str = "INR"
    price_list: float | None = None   # struck-through / undiscounted price, if shown
    is_member: bool | None = None
    is_refundable: bool | None = None
    breakfast_included: bool | None = None
    rooms_left: int | None = None
    room_std: str | None = None       # "A" / "B" once matched
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScrapeResult:
    status: str
    offers: list[Offer] = field(default_factory=list)
    raw: Any = None                   # JSON-serialisable payload(s) kept for re-parsing
    error: str = ""
    page_url: str = ""
    page_title: str = ""
