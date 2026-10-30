# Hotel price scraper: direct booking sites, Indian micromarkets

Each day this tool opens hotels' own booking pages. For each hotel it records the price of two standard rooms (A = entry category, B = next category up) at 1-week, 1-month, 3-month and 6-month horizons, and segments the results by micromarket. It uses no paid APIs. A real browser (Playwright) loads each booking page the way a visitor would, and the tool reads the price data that the page itself loads.

## What works today

| Chain(s) | Booking engine | Module | Status |
|---|---|---|---|
| ITC Hotels | SynXis (`res.itchotels.com`) | `synxis` | ✅ tested end-to-end |
| Oberoi / Trident (EIH) | SynXis (`res.oberoihotels.com`) | `synxis` | ✅ tested end-to-end |
| The Leela (Schloss) | SynXis (`reservations.theleela.com`) | `synxis` | ⚠️ worked once, then blocked a cloud server. Confirm on your computer. |
| Lemon Tree / Aurika / Keys / Red Fox | RezTrip (`reservations.lemontreehotels.com`) | `reztrip` | ✅ tested end-to-end |
| Taj (IHCL) | tajhotels.com | `taj` | 🔧 blocks cloud servers. Finish on your computer (see step 2). |
| Marriott (Chalet, Samhi, Ventive, …) | marriott.com | `marriott` | 🔧 same |
| Hyatt (Juniper) | hyatt.com | `hyatt` | 🔧 same |

The draft list in `config/hotels.yaml` has 32 hotels across 5 micromarkets: Mumbai Airport, Mumbai South/BKC, Delhi Central, Delhi Aerocity/Gurugram and Bengaluru CBD. 18 are confirmed working. The 14 hotels marked `verified: false` (Leela, Taj, Marriott, Hyatt) are skipped by the daily run until you confirm them.

**What you get from each engine:**
- **SynXis** gives every room × rate plan, pre-tax and tax-inclusive prices, breakfast and cancellation terms, member/public flags, and rooms left in inventory.
- **RezTrip** gives each room's cheapest rate, plus the undiscounted price when a promotion is running.

## Setup (your computer)

Python 3.10+ is required.

```bash
cd hotel_price_scraper
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
python -m pytest -q
```

On Windows, activate the virtual environment with `.venv\Scripts\activate` instead of the `source` line.

## Step 1: daily scrape of the working chains

Check the schedule and the booking URLs without opening any pages:

```bash
python -m hps run --dry-run
```

Run a quick live test (2 check-in dates):

```bash
python -m hps run --max-dates 2
```

The full daily run, followed by the Excel export:

```bash
python -m hps run --export
```

A full run of the 18 verified hotels is 144 page loads. With the polite 6–15 s gap between pages, that takes about 45 minutes.

### Schedule it
- **Windows:** register `scripts\run_daily.bat` with Task Scheduler. The exact `schtasks` command is at the top of that file.
- **macOS / Linux:** add `scripts/run_daily.sh` to cron. The example cron line is at the top of the script. A Mac must be awake at that time; use *System Settings → Energy → Schedule* or `pmset` to wake it.

Each run writes a health summary to `data/reports/` with jobs per engine and the success rate. Read it occasionally: a site redesign shows up there as `error` or `parse_error`.

## Step 2: Taj, Marriott, Hyatt and Leela (run on your computer)

These sites block cloud/datacenter IPs, so they have to be finished from your home connection.

**1. Run the one-command site test for each chain.** It opens a visible browser for two hotels at one check-in date.

```bash
python -m hps test-site taj
```

```bash
python -m hps test-site marriott
```

```bash
python -m hps test-site hyatt
```

```bash
python -m hps test-site synxis --hotel leela-mumbai --hotel leela-palace-delhi
```

Each test writes `probe_reports/<chain>_<time>/`, with a `summary.md` (verdict, page title, captured data, prices it found), screenshots, the HTML and every JSON response, plus a `.zip` of the folder.

**2. Act on the verdict:**
- **OK with sensible prices:** set `verified: true` for those hotels in `config/hotels.yaml`, then set their rooms with `python -m hps rooms <hotel_id>`.
- **PARSE_ERROR, or odd prices:** the page loaded, but the generic parser is guessing. Share the `.zip` so a dedicated parser can be written from the real responses. That parser is the only remaining code work for these chains.
- **BLOCKED:** set `browser.channel: chrome` in `config/settings.yaml` to use your installed Google Chrome, and run from home rather than over a VPN. If it still blocks, leave that chain disabled.
- **Wrong page / no URL** (expected for Taj, whose URL format isn't known yet): in your normal browser, search the hotel on the chain's site, open the rooms-and-rates page, copy the address bar, and run:

  ```bash
  python -m hps learn-url taj-mahal-palace "<copied URL>" 2026-12-01 2026-12-02
  ```

  Use the check-in and check-out dates you searched. The tool replaces those dates in the URL with placeholders and saves the result to `config/url_templates.yaml`.

**3. Check the property codes.** The Marriott (`BOMSA`, `DELJW`, `BLRJW`) and Hyatt (`bomgh`, `delaz`) codes in `hotels.yaml` are best guesses. The code is the 5-character prefix in the hotel's URL on marriott.com or hyatt.com.

## Commands

| Command | What it does |
|---|---|
| `python -m hps run [--hotel ID] [--source synxis] [--micromarket mum-airport] [--max-dates N] [--headed] [--export]` | Scrape |
| `python -m hps test-site <source> [--hotel ID] [--headless]` | Probe a chain's site and write a report |
| `python -m hps rooms <hotel_id>` | List a hotel's rooms over 3 sample dates and suggest the A/B rules |
| `python -m hps discover synxis <base_url> <chain>` | List a SynXis chain's hotel IDs (e.g. `https://res.itchotels.com 26676`) |
| `python -m hps discover lemontree <city page URL>` | List Lemon Tree property codes (e.g. `https://www.lemontreehotels.com/mumbai-hotels`) |
| `python -m hps learn-url <hotel_id> "<url>" <checkin> <checkout>` | Store a booking URL template |
| `python -m hps export` | Write the Excel/CSV outputs |

## Configuration

- `config/settings.yaml`:
  - **Horizons:** 1w, 1m, 3m, 6m. `every_n_days` thins out the far horizons.
  - **Check-in mode:** `dow_anchored` (default) uses the first Tuesday and the first Saturday on or after each horizon. That gives clean weekday and weekend series. `fixed_offset` uses today + N days exactly.
  - **Stay:** 1 night, 2 adults, INR.
  - **Rate policy:** excludes member, non-refundable and package rates from the standard price.
  - Browser and pacing settings.
- `config/micromarkets.yaml`: add micromarkets freely.
- `config/hotels.yaml`: one entry per hotel, with its engine identifiers and A/B room rules.

To add a hotel:
1. Find its ID with `discover`.
2. Add an entry to `config/hotels.yaml`.
3. Run `python -m hps rooms <id>` and paste the suggested `rooms:` block into the entry.

Sample several dates when setting rooms: on a busy date the entry category is often sold out, which makes the next category look like the entry one.

## Data

`data/hotel_prices.sqlite` has these tables:

| Table | Contents |
|---|---|
| `jobs` | One row per hotel × check-in date per day: status (`ok`, `no_availability`, `blocked`, `error`, `parse_error`, `skipped`), timings, and the path to the raw response |
| `offers` | Every room × rate seen: room and rate names, member/refundable/breakfast flags, pre-tax and tax-inclusive price, list price, rooms left |
| `std_prices` | **The analysis table.** Per scrape date × hotel × check-in date, one price each for room A, room B and LOWEST (the cheapest eligible offer). Includes micromarket, company, horizon, lead days and check-in weekday. `available = 0` means the room was not on sale (sold out or closed), which is itself a demand signal. |

Raw responses are saved gzipped under `data/raw/<date>/…`, so history can be re-parsed if a parser is improved. Prices are per night in INR. `price_pre_tax` is the comparable figure, because GST slabs vary with the room rate.

The Excel export in `exports/` has these sheets:
- **Snapshot:** hotel × room × horizon, for the latest day
- **Micromarket index:** average room-A price by micromarket and horizon, over time
- **Sold-out share**
- **Booking curves (A):** for each stay night, the room-A price at each lead time. This fills in as daily history builds up.
- **All standard prices**
- **Run health**

## Notes and limits
- **Pacing:** one browser, one page at a time, random delays, and no logins. The run stops visiting a site for the rest of the night after 3 consecutive blocks. Keep it gentle: this reads public prices, but it still breaches most sites' terms of service.
- **Member rates** on brand sites are usually 5–15% below the public rate. They're stored but excluded from the standard price.
- **Lemon Tree's engine** returns only the cheapest rate per room, so its standard price is "cheapest public rate". Refundability is read from the policy text.
- **Automatic room choice:** hotels without A/B rules get the cheapest and second-cheapest rooms of the day (`method = auto`), which is less stable than configured rules.
