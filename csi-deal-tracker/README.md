# CSI Deal Tracker

Scrapes GlobeNewsWire press releases for acquisition announcements from
Constellation Software and its operating groups, persisting results in a local
SQLite database for trend analysis.

## Covered entities

| Operating group | Entity scraped |
|-----------------|---------------|
| CSI (parent)    | Constellation Software Inc. |
| Volaris         | Volaris Group Inc. |
| Harris          | Harris Computer |
| Jonas           | Jonas Software |
| Perseus         | Perseus Group |
| Topicus         | Topicus.com Inc. |
| Lumine          | Lumine Group Inc. |
| Andromeda       | Andromeda |

## Project layout

```
csi-deal-tracker/
├── sources.py       # URL registry (RSS + search URLs per entity)
├── db.py            # SQLite schema, upsert, and query helpers
├── scraper.py       # GlobeNewsWire scraper (RSS-first, HTML fallback)
├── requirements.txt
└── deals.db         # Created automatically on first run
```

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the scraper
python scraper.py
```

Output:
```
10:42:01  INFO    ── Scraping: Constellation Software (CSI (parent))
10:42:02  INFO       [NEW] 2024-11-14T13:00:00+00:00 — Harris Computer Acquires XYZ Ltd
...
Scrape summary:
  total_fetched      : 47
  new_inserted       : 12
  total_in_db        : 12
  sources_scraped    : 8
  run_at             : 2024-11-18T10:42:09+00:00
```

## Querying the database

```python
from db import fetch_deals, deal_count

# All deals, most recent first
deals = fetch_deals(limit=50)

# Harris only, since 2024
harris_deals = fetch_deals(group_name="Harris", since="2024-01-01")

# Total stored
print(deal_count())
```

## Data sources

All press releases are sourced from **GlobeNewsWire**:

- **RSS feeds**: `https://www.globenewswire.com/RssFeed/organization/<slug>`
  Structured, date-stamped; preferred source.
- **Search pages**: `https://www.globenewswire.com/search/news?q=...`
  HTML fallback used when the RSS feed returns zero acquisition results.

## Acquisition filter

A press release is classified as an acquisition if its title or summary
contains any of the following keywords (case-insensitive):

- `acqui` (covers *acquires*, *acquisition*, *acquired*)
- `purchase`
- `completes acquisition`
- `closed acquisition`
- `announces acquisition`

## Scheduling

To run daily (cron example):
```cron
0 8 * * * cd /path/to/csi-deal-tracker && python scraper.py >> scraper.log 2>&1
```
