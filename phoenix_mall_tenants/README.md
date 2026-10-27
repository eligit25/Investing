# Phoenix Mills tenant tracker

Scrapes the **current** store directory of every Phoenix Mills (NSE: PHOENIXLTD) retail
asset and reconstructs **historical** tenant lists from the Internet Archive's Wayback
Machine. Use it to track openings, closures, churn, category mix and premiumisation over
time.

## Is it possible?

**Current tenants: yes, and it works today.** All 13 operating malls expose their directory
through JSON endpoints that the sites' own JavaScript calls. On 2026-10-27 the scraper pulled
2,896 tenants (retail + dining) with category and floor.

**Historical tenants: yes, with gaps you need to understand.** The current sites load their
brand lists by a background POST request after the page opens. The Wayback Machine archives
the page shell but generally not that POST response, so an archived `/brands` page from
2023 is usually empty. The tool therefore combines three methods (see below). The most
robust is an inventory of every archived *tenant detail page*, which gives first-seen /
last-seen dates per brand without parsing any listing.

The most reliable history, though, is the one you build going forward: run `current`
monthly. Each run is a complete, dated snapshot.

## Coverage (live, 2026-10-27)

| mall_id | Mall | Platform | Tenants (dining) |
|---|---|---|---|
| pmc_bangalore | Phoenix Marketcity Bangalore | aspnet | 285 (49) |
| pmc_chennai | Phoenix Marketcity Chennai | aspnet | 238 (46) |
| palladium_mumbai | Palladium Mumbai, Lower Parel | aspnet | 283 (47) |
| palladium_ahmedabad | Palladium Ahmedabad | aspnet | 211 (49) |
| mall_of_millennium_pune | Mall of the Millennium, Wakad | aspnet | 306 (65) |
| united_lucknow | Phoenix United Lucknow | aspnet | 68 (9) |
| united_bareilly | Phoenix United Bareilly | aspnet | 100 (16) |
| pmc_mumbai | Phoenix Marketcity Mumbai, Kurla | website_mvc | 255 (54) |
| pmc_pune | Phoenix Marketcity Pune (Avenue of Stars) | website_mvc | 265 (56) |
| palassio_lucknow | Phoenix Palassio | website_mvc | 244 (40) |
| citadel_indore | Phoenix Citadel / Pride of India | citadel_mvc | 261 (41) |
| mall_of_asia_bangalore | Phoenix Mall of Asia, Hebbal | moa_json | 342 (69) |
| palladium_chennai | Palladium Chennai | wordpress | 38 (5) ⚠ site is sparsely maintained |

Counts are *listings*. A brand with two units in one mall (two Starbucks) is one brand-level
tenant in the analytics. `data/current/2026-10-27/tenants.csv` is the first snapshot.

## Quick start

```bash
cd phoenix_mall_tenants
pip install -r requirements.txt

python -m phoenix_tenants list                       # mall registry
python -m phoenix_tenants current                    # -> data/current/<date>/tenants.csv
python -m phoenix_tenants current --malls pmc_mumbai palladium_mumbai

# history (needs access to web.archive.org)
python -m phoenix_tenants wayback-details  --since 2013
python -m phoenix_tenants wayback-listings --since 2013 --freq Q
python -m phoenix_tenants wayback-discover --malls palladium_mumbai citadel_indore
python -m phoenix_tenants history --confirm 2       # -> data/history/{summary,events,observations}.csv

python -m phoenix_tenants diff data/current/2026-10-27/tenants.csv data/current/2026-11-27/tenants.csv
```

Run the tests (offline, no network): `python -m pytest -q tests`

## Output columns

`tenants.csv`: `mall_id, mall_name, city, tenant_type (retail|dine|luxury), name, name_key,
category_raw, category, floor, source_id, detail_url, listed_since_hint, platform, as_of, source`

* `category` maps each site's free-text categories onto one taxonomy (Apparel, Food & Beverage,
  Watches & Jewellery, Beauty & Personal Care, ...). Rules are in `normalize.py`.
  `category_raw` keeps the original.
* `listed_since_hint` gives a free historical signal on some platforms:
  * Indore's API returns each listing's `createdOn` date.
  * The legacy ASP.NET CMS names logo uploads `ddMMyyyy…_brlo.png`, so the upload date can
    be recovered. For a new tenant that is roughly its listing date. For an old tenant it may
    only be a logo refresh, so treat it as a lower bound.
* `name_key` is a normalised brand key used to match the same tenant across snapshots
  ("H & M" → `hm`, "adidas-indore" → `adidas`). `history` also merges spelling variants
  ("Van Huesen" → "Van Heusen") but never whole-word differences ("Van Heusen Men" ≠ "Van
  Heusen Women").

`history/summary.csv`: per series `family` (`listing` = full directory snapshots from live
scrapes and archived listings; `details` = quarterly presence derived from archived detail-page
windows; the two are never compared with each other), mall and snapshot date, `n_tenants, n_open, n_close, churn_pct,
luxury_premium_pct` and `cat:<Category>` share columns. `history/events.csv` lists every
opening and closure.

## How the historical reconstruction works

| Method | Command | What it gives | Works for | Blind spots |
|---|---|---|---|---|
| Detail-page inventory | `wayback-details` | first/last date each tenant's own page was archived, last HTTP status (404 ⇒ `likely_exited`) | every platform with per-tenant URLs; JS-rendered listings are no problem | crawl frequency is uneven (first-seen can lag an opening); stale pages can outlive a closure |
| Listing snapshots | `wayback-listings` | full tenant list per quarter (or month / year) | server-rendered directories (Palladium Chennai, older static sites), archived GET JSON (Mall of Asia API) | current ASP.NET/MVC listing pages archive as empty shells; these are logged as `empty_shell` in `*_snapshots.csv` |
| Discovery | `wayback-discover` | every archived directory-like URL on a host, grouped by path pattern | legacy domains: `highstreetphoenix.com` (2013–21), `palladiummumbai.com` (2014–20), `phoenixcitadel.com` (2014–19), `phoenixmarketcitypune.com` (2014–16), pre-2019 paths on `phoenixmarketcity.com` | you then add the found patterns via `--listing-url` / `--detail-prefix` (or in `malls.py`) |

Listing snapshots pick the last good capture in each period, fetch the raw archived bytes
(`/web/<ts>id_/<url>`, no toolbar or link rewriting) and cache them by digest under
`data/cache/`. Periods whose content is unchanged are parsed once. Parsing uses a generic
extractor: any link pointing at a tenant detail page counts as a tenant. That holds up
across redesigns.

**Recommended workflow for an investment-grade time series**

1. Start a monthly `current` run now (cron / GitHub Action). This is your clean series from here on.
2. Run `wayback-details` for every mall to get presence windows back to each site's launch.
3. Run `wayback-discover` on the legacy hosts, add the directory URLs it finds, then run
   `wayback-listings` to fill pre-2019 eras. This matters most for Palladium Mumbai / HSP,
   Indore and PMC Pune.
4. Run `history --confirm 2`, so a tenant has to be missing from two consecutive snapshots
   before it counts as closed. Compare tenant counts and category mix against the company's
   disclosed trading occupancy and consumption numbers as a sanity check.

## Caveats when interpreting

* A website directory is a *marketing* list, not a rent roll. It lags openings, sometimes keeps
  closed stores, and can list fit-out stores early. Use it for direction and mix, not for exact
  occupancy.
* Comparisons between snapshots only use tenant types present in both (if an old site listed
  only retail, dining churn isn't inferred from its absence). `tenant_types` and `sources`
  columns in `summary.csv` show what each snapshot covered. Treat jumps that coincide with a
  source change as suspect.
* Category mapping is keyword-based. Check `Other` / `Unknown` (≈3% of rows today) and
  refine `_CATEGORY_RULES` as needed.
* `brand_tiers.csv` is a small seed list (global luxury / premium / bridge names) behind
  `luxury_premium_pct`. Extend it for a meaningful premiumisation metric.

## Notes on the sites

* Several sites send an incomplete TLS chain (leaf certificate only), so Python can't verify
  them out of the box. `phoenix_tenants/certs/` ships the two missing public intermediates
  (Sectigo DV R36, RapidSSL G1), which are appended to the normal trust store. Verification
  stays on.
* The ASP.NET sites sit behind an F5 WAF that rejects API calls without a `Referer`. The mall
  is selected by a session cookie set when you GET the mall's `/brands` page.
* The legacy dine API includes `DineLoginEmail` / `DineLoginPassword` fields (empty in
  what we saw). The scraper deliberately drops them, and no phone numbers or emails are stored.
* `robots.txt` on every site that serves one allows general crawling. The scraper sends about
  one request per second per host, and 1.5 s for the Wayback Machine (which rate-limits with
  HTTP 429 and temporary blocks).

## Complementary sources

* **Phoenix Mills quarterly investor presentations**: trading occupancy, consumption and
  trading density per mall, plus marquee new-brand call-outs. Use these to validate the
  website-derived series.
* **Common Crawl** (`index.commoncrawl.org`): a second archive with different crawl timing,
  useful to fill Wayback gaps for detail pages.
* **archive.today**: occasional manual captures of JS-rendered pages.

## Layout

```
phoenix_tenants/
  malls.py       registry: 13 malls, platform, legacy domains, listing/detail URL patterns
  http.py        throttled session with retries + CA bundle fix
  platforms.py   5 live adapters -> normalised rows
  wayback.py     CDX client, snapshot fetch/cache, archived-listing parser, detail inventory, discovery
  history.py     name reconciliation, presence, open/close events, churn, category/tier mix
  normalize.py   name keys, category taxonomy, date hints
  cli.py         python -m phoenix_tenants ...
tests/           offline tests (fixtures + mocked CDX)
data/current/    dated live snapshots (committed); data/cache, data/history are git-ignored
```
