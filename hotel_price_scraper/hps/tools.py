"""Setup helpers: site tests (probes), ID discovery, room suggestions, URL templates."""
from __future__ import annotations

import json
import logging
import random
import re
import shutil
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from .adapters import ADAPTERS
from .browser import BrowserSession
from .config import CONFIG_DIR, ROOT, Config, Hotel
from .dates import Stay, next_weekday_on_or_after
from .rooms import is_eligible

log = logging.getLogger("hps")
URL_TEMPLATES = CONFIG_DIR / "url_templates.yaml"


def _probe_stay(cfg: Config, days: int = 30) -> Stay:
    checkin = next_weekday_on_or_after(date.today() + timedelta(days=days), 1)  # a Tuesday ~1 month out
    return Stay(checkin, checkin + timedelta(days=int(cfg.settings["stay"]["nights"])), "probe", days, "tue")


# --------------------------------------------------------------------------- test-site

def test_site(cfg: Config, source: str, hotel_ids: list[str] | None = None, headed: bool = True,
              max_hotels: int = 2) -> Path:
    """Open real booking pages for a chain and write a probe report folder (+ .zip)."""
    hotels = [h for h in cfg.hotels if h.source == source and (not hotel_ids or h.id in hotel_ids)][:max_hotels]
    if not hotels:
        raise SystemExit(f"no hotels with source={source!r} in hotels.yaml")
    stay = _probe_stay(cfg)
    out = ROOT / "probe_reports" / f"{source}_{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True)
    lines = [f"# Site test: {source}", "", f"Run at {datetime.now():%Y-%m-%d %H:%M}, check-in {stay.checkin} "
             f"({stay.checkin:%a}), {'visible' if headed else 'headless'} browser.", ""]
    verdicts = []
    with BrowserSession(cfg.settings["browser"], headless=not headed) as session:
        adapter = ADAPTERS[source](session, cfg.settings)
        adapter.warm_up(hotels[0])
        for h in hotels:
            try:
                url = adapter.url(h, stay)
                note = ""
            except ValueError as e:
                url = h.params.get("hotel_page", "")
                note = str(e)
            lines += [f"## {h.name} (`{h.id}`)", "", f"- URL: {url or '(none)'}"]
            if note:
                lines.append(f"- Note: {note}")
            if not url:
                verdicts.append((h.id, "no URL"))
                continue
            page = session.new_page()
            cap = session.capture(url, adapter.wanted(h, stay), wait_for_first=False, settle_s=25,
                                  keep_all_json=True, page=page)
            try:
                page.screenshot(path=str(out / f"{h.id}.png"), full_page=True)
                (out / f"{h.id}.html").write_text(page.content(), encoding="utf-8")
            except Exception as e:  # noqa: BLE001
                lines.append(f"- Screenshot failed: {e}")
            page.close()
            (out / f"{h.id}_responses.json").write_text(json.dumps(cap.responses, indent=1)[:40_000_000], encoding="utf-8")
            result = adapter.result_from_capture(h, stay, cap)
            verdict = "BLOCKED" if cap.blocked else result.status.upper()
            verdicts.append((h.id, verdict))
            lines += [f"- Landed on: {cap.url}", f"- HTTP status: {cap.nav_status}   Page title: {cap.title!r}",
                      f"- Verdict: **{verdict}**  ({result.error or 'ok'})",
                      f"- JSON responses captured: {len(cap.responses)} "
                      f"({sum(r['wanted'] for r in cap.responses)} matched the price patterns)", ""]
            if result.offers:
                lines += ["| room | rate | pre-tax | incl. tax |", "|---|---|---|---|"]
                for o in sorted(result.offers, key=lambda o: o.price_pre_tax or 9e9)[:15]:
                    lines.append(f"| {o.room_name} | {o.rate_name} | {o.price_pre_tax} | {o.price_incl_tax} |")
                lines.append("")
            biggest = sorted(cap.responses, key=lambda r: -len(json.dumps(r["json"])))[:12]
            if biggest:
                lines += ["Largest JSON responses (likely candidates for the rate data):", ""]
                lines += [f"- {'[match] ' if r['wanted'] else ''}{len(json.dumps(r['json'])):>8,} B  {r['url'][:160]}"
                          for r in biggest]
                lines.append("")
    lines += ["## Summary", ""] + [f"- {hid}: {v}" for hid, v in verdicts] + ["", NEXT_STEPS]
    (out / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    shutil.make_archive(str(out), "zip", out)
    print("\n".join(lines))
    print(f"\nReport folder: {out}\nZip to share:  {out}.zip")
    return out


NEXT_STEPS = """## What to do next

- **OK** with sensible prices: the chain works from this computer. Set `verified: true` for its hotels in
  config/hotels.yaml and confirm the room A/B rules (`python -m hps rooms <hotel_id>`).
- **OK / PARSE_ERROR with odd prices**: the page loaded but the generic parser is guessing. Share the .zip
  so a dedicated parser can be written from the captured responses.
- **BLOCKED**: try `--headed` with `browser.channel: chrome` in settings.yaml (your installed Chrome), and
  run from your home connection rather than a VPN or office network. If it still blocks, leave that chain
  disabled rather than escalating.
- **No URL / wrong page**: search the hotel on the chain's website in your normal browser, open the rooms
  and rates page, copy the address bar and run:
  `python -m hps learn-url <hotel_id> "<copied URL>" <checkin YYYY-MM-DD> <checkout YYYY-MM-DD>`
"""


# --------------------------------------------------------------------------- learn-url

DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%d-%m-%Y", "%m-%d-%Y", "%Y%m%d", "%d%m%Y"]


def url_to_template(url: str, checkin: date, checkout: date) -> str:
    """Replace the given dates (in any common format, URL-encoded or not) with format fields."""
    if checkin == checkout:
        raise ValueError("checkin and checkout must differ")
    tpl = url.replace("{", "{{").replace("}", "}}")
    replaced = 0
    for fmt in DATE_FORMATS:
        for d, field in ((checkin, "checkin"), (checkout, "checkout")):
            plain = "{%s:%s}" % (field, fmt)
            # URL-encoded slashes: format each part separately and keep the literal %2F between them.
            encoded = "%2F".join("{%s:%s}" % (field, part) for part in fmt.split("/"))
            for literal, placeholder in ((d.strftime(fmt), plain), (d.strftime(fmt).replace("/", "%2F"), encoded)):
                if literal in tpl:
                    tpl = tpl.replace(literal, placeholder)
                    replaced += 1
    if not replaced:
        raise ValueError("neither date was found in the URL - check the dates you passed")
    return tpl


def learn_url(cfg: Config, hotel_id: str, url: str, checkin: str, checkout: str) -> str:
    hotel = cfg.hotel(hotel_id)
    tpl = url_to_template(url, date.fromisoformat(checkin), date.fromisoformat(checkout))
    data = {}
    if URL_TEMPLATES.exists():
        data = yaml.safe_load(URL_TEMPLATES.read_text(encoding="utf-8")) or {}
    data[hotel.id] = tpl
    URL_TEMPLATES.write_text("# Written by `python -m hps learn-url`. Overrides params.url_template.\n"
                             + yaml.safe_dump(data, sort_keys=True, allow_unicode=True), encoding="utf-8")
    return tpl


# --------------------------------------------------------------------------- discovery

LEMONTREE_CARDS_JS = r"""() => [...document.querySelectorAll('[data-hotel-code]')].map(el => {
  let card = el;
  for (let i = 0; i < 12 && card; i++) {
    if (card.querySelectorAll && [...card.querySelectorAll('a[href]')].some(a => /\/[a-z-]+\/[a-z-]+\/[a-z0-9-]+$/.test(a.href))) break;
    card = card.parentElement;
  }
  const link = card ? [...card.querySelectorAll('a[href]')].find(a => /lemontreehotels\.com\/[a-z-]+\/[a-z-]+\/[a-z0-9-]+$/.test(a.href)) : null;
  return {code: el.dataset.hotelCode || '', page: link ? link.href : ''};
}).filter(r => r.code)"""


def discover_lemontree(city_pages: list[str], headed: bool = False, settings: dict | None = None) -> list[dict]:
    """Read property codes from Lemon Tree city pages (e.g. https://www.lemontreehotels.com/mumbai-hotels).

    Each hotel card on a city page carries its booking-engine code (data-hotel-code).
    """
    out: dict[str, dict] = {}
    with BrowserSession((settings or {}).get("browser", {}), headless=not headed) as session:
        page = session.new_page()
        for url in city_pages:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            for row in page.evaluate(LEMONTREE_CARDS_JS):
                out.setdefault(row["code"], {**row, "city_page": url})
        page.close()
    return list(out.values())


def discover_synxis(base_url: str, chain: str, headed: bool = False, settings: dict | None = None) -> list[dict]:
    """List a SynXis chain's hotels (id, name, city) from its own hotel-selection page."""
    start = date.today() + timedelta(days=30)
    url = (f"{base_url.rstrip('/')}/?chain={chain}&level=chain&adult=2&rooms=1&currency=INR&locale=en-US"
           f"&arrive={start}&depart={start + timedelta(days=1)}")
    hotels: dict[int, dict] = {}
    with BrowserSession((settings or {}).get("browser", {}), headless=not headed) as session:
        page = session.new_page()
        cap = session.capture(url, lambda r: "QueryHotelList" in r.url, wait_for_first=True, settle_s=5, page=page)
        # The page lists hotels in batches as you scroll; scroll to the bottom a few times.
        for _ in range(15):
            page.mouse.wheel(0, 20000)
            page.wait_for_timeout(1500)
        page.close()
    for r in cap.responses:
        for h in (r["json"].get("HotelList") or []):
            bp = h.get("BasicPropertyInfo", {})
            addr = bp.get("Location", {}).get("Address", {})
            hid = h.get("Hotel", {}).get("Id") or h.get("Id")
            hotels[hid] = {"id": hid, "name": h.get("Hotel", {}).get("Name") or h.get("Name"),
                           "city": addr.get("City"), "status": bp.get("Status")}
    return sorted(hotels.values(), key=lambda h: (h["city"] or "", h["name"] or ""))


def suggest_rooms(cfg: Config, hotel: Hotel, headed: bool = False, lead_days=(10, 45, 120)) -> str:
    """Scrape a few dates and print the room list with a suggested A/B mapping.

    Several dates are sampled because a hotel's entry category is often sold out
    on any single busy date, which would make the next category look like the entry one.
    """
    stays = [_probe_stay(cfg, d) for d in lead_days]
    policy = cfg.settings["rate_policy"]
    results = []
    with BrowserSession(cfg.settings["browser"], headless=not headed) as session:
        adapter = ADAPTERS[hotel.source](session, cfg.settings)
        adapter.warm_up(hotel)
        for i, stay in enumerate(stays):
            if i:
                time.sleep(random.uniform(*cfg.settings["browser"].get("delay_s", [6, 15])))
            results.append((stay, adapter.scrape(hotel, stay)))
    if not any(res.offers for _, res in results):
        return f"{hotel.id}: " + "; ".join(f"{s.checkin} {r.status} {r.error}" for s, r in results)
    rooms: dict[str, dict] = {}
    for stay, res in results:
        for o in res.offers:
            r = rooms.setdefault(o.room_code or o.room_name, {"code": o.room_code, "name": o.room_name,
                                                              "best": None, "dates": set()})
            r["dates"].add(stay.checkin)
            if is_eligible(o, policy) and (r["best"] is None or o.price_pre_tax < r["best"]):
                r["best"] = o.price_pre_tax
    ranked = sorted(rooms.values(), key=lambda r: r["best"] or 9e12)
    excl = [re.compile(p, re.I) for p in policy.get("exclude_room_patterns", [])]
    candidates = [r for r in ranked if r["best"] and not any(p.search(r["name"]) for p in excl)]
    lines = [f"{hotel.name} - check-ins " + ", ".join(f"{s.checkin} ({r.status})" for s, r in results), "",
             f"{'code':10s} {'room':45s} {'cheapest public':>16s}  on sale"]
    for r in ranked:
        best = f"₹{r['best']:,.0f}" if r["best"] else "-"
        lines.append(f"{r['code']:10s} {r['name'][:45]:45s} {best:>16s}  {len(r['dates'])}/{len(stays)} dates")
    if candidates:
        lines += ["", "Suggested hotels.yaml rooms block (A = entry category, B = next category up):", "    rooms:"]
        for std, r in zip(("A", "B"), candidates):
            key = f"codes: [{r['code']}]" if r["code"] else f"names: ['^{re.escape(r['name'])}$']"
            lines.append(f"      {std}: {{{key}}}   # {r['name']}")
    return "\n".join(lines)
