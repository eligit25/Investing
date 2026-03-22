"""
GlobeNewsWire scraper for CSI acquisition press releases.

Strategy (per source)
---------------------
1. Fetch the organization RSS feed — clean, structured, no JS required.
2. Parse <item> entries; filter by ACQUISITION_KEYWORDS.
3. Fallback to HTML search page if RSS returns 0 results.
4. Persist new deals via db.upsert_deal().
"""

import re
import time
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

from db import init_db, upsert_deal, deal_count
from sources import SOURCES, ACQUISITION_KEYWORDS

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.9",
}

REQUEST_TIMEOUT = 20   # seconds
POLITE_DELAY    = 1.2  # seconds between requests


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_acquisition(title: str, summary: str = "") -> bool:
    text = (title + " " + summary).lower()
    return any(kw in text for kw in ACQUISITION_KEYWORDS)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _parse_rfc2822_date(raw: str) -> str | None:
    """Convert RFC-2822 date string (RSS pubDate) to ISO-8601."""
    try:
        return parsedate_to_datetime(raw).isoformat()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# RSS feed parser
# ---------------------------------------------------------------------------

def _scrape_rss(session: requests.Session, source: dict) -> list[dict]:
    """Fetch and parse a GlobeNewsWire RSS feed; return acquisition items."""
    url = source["rss_url"]
    try:
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("[rss] %s — fetch failed: %s", source["entity"], exc)
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        log.warning("[rss] %s — XML parse error: %s", source["entity"], exc)
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    channel = root.find("channel")
    if channel is None:
        return []

    items = []
    for item in channel.findall("item"):
        title   = _clean(item.findtext("title", ""))
        link    = _clean(item.findtext("link", ""))
        guid    = _clean(item.findtext("guid", link))
        pub_raw = item.findtext("pubDate", "")
        desc    = _clean(item.findtext("description", ""))[:500]

        if not _is_acquisition(title, desc):
            continue

        items.append({
            "guid":         guid or link,
            "published_at": _parse_rfc2822_date(pub_raw),
            "entity":       source["entity"],
            "group_name":   source["group"],
            "title":        title,
            "summary":      desc,
            "url":          link,
            "source":       "gnw_rss",
        })

    log.info("[rss] %s — %d acquisition items found", source["entity"], len(items))
    return items


# ---------------------------------------------------------------------------
# HTML search page parser (fallback)
# ---------------------------------------------------------------------------

def _scrape_search(session: requests.Session, source: dict) -> list[dict]:
    """Scrape GlobeNewsWire search results page as a fallback."""
    url = source["search_url"]
    try:
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("[search] %s — fetch failed: %s", source["entity"], exc)
        return []

    soup = BeautifulSoup(resp.text, "lxml")

    # GlobeNewsWire search results are inside <article> or <li class="news-item">
    articles = soup.select("article.news-article, li.news-item, div.result-item")
    if not articles:
        # Broader fallback selector
        articles = soup.select("div[class*='article'], div[class*='news']")

    items = []
    for art in articles:
        link_tag = art.select_one("a[href*='/news-release/']")
        if not link_tag:
            continue

        title = _clean(link_tag.get_text())
        href  = link_tag["href"]
        if not href.startswith("http"):
            href = "https://www.globenewswire.com" + href

        date_tag = art.select_one("time, span[class*='date']")
        pub_raw  = date_tag.get("datetime", "") if date_tag else ""

        summary_tag = art.select_one("p, div[class*='summary'], div[class*='desc']")
        summary = _clean(summary_tag.get_text())[:500] if summary_tag else ""

        if not _is_acquisition(title, summary):
            continue

        items.append({
            "guid":         href,
            "published_at": pub_raw or None,
            "entity":       source["entity"],
            "group_name":   source["group"],
            "title":        title,
            "summary":      summary,
            "url":          href,
            "source":       "gnw_search",
        })

    log.info("[search] %s — %d acquisition items found", source["entity"], len(items))
    return items


# ---------------------------------------------------------------------------
# Main scrape orchestrator
# ---------------------------------------------------------------------------

def run_scrape(verbose: bool = True) -> dict:
    """
    Run full scrape across all sources.

    Returns
    -------
    dict with keys: total_fetched, new_inserted, sources_scraped
    """
    if verbose:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s  %(levelname)-7s %(message)s",
            datefmt="%H:%M:%S",
        )

    init_db()
    session = requests.Session()
    session.headers.update(HEADERS)

    total_fetched  = 0
    new_inserted   = 0

    for source in SOURCES:
        log.info("── Scraping: %s (%s)", source["entity"], source["group"])

        # 1. Try RSS first
        items = _scrape_rss(session, source)

        # 2. Fallback to search page if RSS empty
        if not items:
            log.info("   RSS empty — trying search page fallback")
            time.sleep(POLITE_DELAY)
            items = _scrape_search(session, source)

        total_fetched += len(items)

        for deal in items:
            if upsert_deal(deal):
                new_inserted += 1
                if verbose:
                    log.info("   [NEW] %s — %s", deal["published_at"], deal["title"][:70])

        time.sleep(POLITE_DELAY)

    summary = {
        "total_fetched":   total_fetched,
        "new_inserted":    new_inserted,
        "total_in_db":     deal_count(),
        "sources_scraped": len(SOURCES),
        "run_at":          datetime.now(timezone.utc).isoformat(),
    }
    log.info("── Done. %d new deals inserted (%d total in DB)", new_inserted, summary["total_in_db"])
    return summary


if __name__ == "__main__":
    result = run_scrape()
    print("\nScrape summary:")
    for k, v in result.items():
        print(f"  {k:<20}: {v}")
