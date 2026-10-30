from __future__ import annotations

from ..browser import BrowserSession, Capture
from ..config import Hotel
from ..dates import Stay
from ..models import BLOCKED, ERROR, NO_AVAILABILITY, OK, PARSE_ERROR, ScrapeResult


class Adapter:
    """One booking engine. Subclasses implement url() and parse()."""

    source: str = ""

    def __init__(self, session: BrowserSession, settings: dict):
        self.session = session
        self.settings = settings
        self.stay_cfg = settings["stay"]

    def warm_up(self, hotel: Hotel) -> None:
        """Optional one-off visit before the first booking page of a run."""

    def url(self, hotel: Hotel, stay: Stay) -> str:
        raise NotImplementedError

    def wanted(self, hotel: Hotel, stay: Stay):
        """Predicate selecting the response(s) that carry prices."""
        raise NotImplementedError

    def parse(self, hotel: Hotel, stay: Stay, payloads: list) -> ScrapeResult:
        raise NotImplementedError

    def scrape(self, hotel: Hotel, stay: Stay) -> ScrapeResult:
        cap = self.session.capture(self.url(hotel, stay), self.wanted(hotel, stay))
        return self.result_from_capture(hotel, stay, cap)

    def result_from_capture(self, hotel: Hotel, stay: Stay, cap: Capture) -> ScrapeResult:
        wanted = [r["json"] for r in cap.responses if r["wanted"]]
        base = dict(page_url=cap.url, page_title=cap.title)
        if not wanted:
            if cap.blocked:
                return ScrapeResult(BLOCKED, error=f"HTTP {cap.nav_status} / {cap.title!r}", **base)
            return ScrapeResult(ERROR, error=cap.error or "price response never arrived", **base)
        try:
            res = self.parse(hotel, stay, wanted)
        except Exception as e:  # noqa: BLE001 - keep the raw payload for later re-parsing
            return ScrapeResult(PARSE_ERROR, raw=wanted, error=f"{type(e).__name__}: {e}", **base)
        res.raw = res.raw if res.raw is not None else wanted
        res.page_url, res.page_title = cap.url, cap.title
        if res.status == OK and not res.offers:
            res.status = NO_AVAILABILITY
        return res
