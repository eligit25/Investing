"""
Source registry for CSI deal tracker.

Each entry maps an operating group / entity to:
  - rss_url:    GlobeNewsWire RSS feed for the organization
  - search_url: GlobeNewsWire full-text search URL (fallback / supplemental)
"""

# GlobeNewsWire organization slugs (used in RSS feeds)
# Verify at: https://www.globenewswire.com/RssFeed/organization/<slug>
GNW_RSS_BASE   = "https://www.globenewswire.com/RssFeed/organization"
GNW_SEARCH_BASE = "https://www.globenewswire.com/search/news"

SOURCES = [
    {
        "entity":   "Constellation Software",
        "group":    "CSI (parent)",
        "rss_url":  f"{GNW_RSS_BASE}/constellation-software-inc",
        "search_url": f"{GNW_SEARCH_BASE}?q=constellation+software&category=Mergers+%26+Acquisitions",
    },
    {
        "entity":   "Volaris Group",
        "group":    "Volaris",
        "rss_url":  f"{GNW_RSS_BASE}/volaris-group-inc",
        "search_url": f"{GNW_SEARCH_BASE}?q=volaris+group+acqui",
    },
    {
        "entity":   "Harris Computer",
        "group":    "Harris",
        "rss_url":  f"{GNW_RSS_BASE}/harris-computer",
        "search_url": f"{GNW_SEARCH_BASE}?q=harris+computer+acqui",
    },
    {
        "entity":   "Jonas Software",
        "group":    "Jonas",
        "rss_url":  f"{GNW_RSS_BASE}/jonas-software",
        "search_url": f"{GNW_SEARCH_BASE}?q=jonas+software+acqui",
    },
    {
        "entity":   "Perseus Group",
        "group":    "Perseus",
        "rss_url":  f"{GNW_RSS_BASE}/perseus-group",
        "search_url": f"{GNW_SEARCH_BASE}?q=perseus+group+acqui",
    },
    {
        "entity":   "Topicus",
        "group":    "Topicus",
        "rss_url":  f"{GNW_RSS_BASE}/topicus-com-inc",
        "search_url": f"{GNW_SEARCH_BASE}?q=topicus+acqui",
    },
    {
        "entity":   "Lumine Group",
        "group":    "Lumine",
        "rss_url":  f"{GNW_RSS_BASE}/lumine-group-inc",
        "search_url": f"{GNW_SEARCH_BASE}?q=lumine+group+acqui",
    },
    {
        "entity":   "Andromeda",
        "group":    "Andromeda",
        "rss_url":  f"{GNW_RSS_BASE}/andromeda",
        "search_url": f"{GNW_SEARCH_BASE}?q=andromeda+acqui+constellation",
    },
]

# Keywords that indicate an acquisition announcement
ACQUISITION_KEYWORDS = [
    "acqui",       # acquires / acquisition / acquired
    "purchase",
    "completes acquisition",
    "closed acquisition",
    "announces acquisition",
]
