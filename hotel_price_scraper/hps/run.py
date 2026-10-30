"""Daily run: every enabled hotel x every scheduled check-in date."""
from __future__ import annotations

import logging
import random
import socket
import time
from collections import Counter, defaultdict
from datetime import date, datetime

from .adapters import ADAPTERS
from .browser import BrowserSession
from .config import Config, Hotel
from .dates import build_schedule
from .db import Store
from .models import BLOCKED, ERROR, NO_AVAILABILITY, OK, SKIPPED, ScrapeResult
from .rooms import standard_prices

log = logging.getLogger("hps")


def select_hotels(cfg: Config, sources=None, hotel_ids=None, micromarkets=None, include_unverified=False) -> list[Hotel]:
    out = []
    for h in cfg.hotels:
        if not h.enabled:
            continue
        if hotel_ids and h.id not in hotel_ids:
            continue
        if sources and h.source not in sources:
            continue
        if micromarkets and h.micromarket not in micromarkets:
            continue
        if not h.verified and not include_unverified and not hotel_ids:
            continue
        out.append(h)
    return out


def run(cfg: Config, hotels: list[Hotel], *, today: date | None = None, headless: bool | None = None,
        max_dates: int | None = None, dry_run: bool = False, note: str = "") -> dict:
    today = today or date.today()
    settings = cfg.settings
    stays = build_schedule(today, settings)
    if max_dates:
        stays = stays[:max_dates]
    log.info("scrape date %s: %d hotels x %d check-in dates = %d jobs",
             today, len(hotels), len(stays), len(hotels) * len(stays))
    for s in stays:
        log.info("  %-8s check-in %s (%s)", s.horizon_label, s.checkin, s.checkin.strftime("%a"))
    if dry_run:
        for h in hotels:
            adapter_cls = ADAPTERS[h.source]
            for s in stays[:1]:
                try:
                    url = adapter_cls(None, settings).url(h, s)
                except Exception as e:  # noqa: BLE001
                    url = f"<{e}>"
                log.info("  %s -> %s", h.id, url)
        return {}

    store = Store(cfg.path("db_path"), cfg.path("raw_dir"))
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    store.start_run(run_id, today, socket.gethostname(), note)
    policy = settings["rate_policy"]
    bcfg = settings["browser"]
    lo, hi = bcfg.get("delay_s", [6, 15])
    max_blocks = int(bcfg.get("max_consecutive_blocks", 3))
    stats: Counter = Counter()
    by_source: dict[str, Counter] = defaultdict(Counter)

    # Group by website so pacing and block handling apply per site; shuffle within a group.
    groups: dict[str, list[Hotel]] = defaultdict(list)
    for h in hotels:
        groups[h.chain].append(h)

    try:
        with BrowserSession(bcfg, headless=headless) as session:
            adapters = {src: cls(session, settings) for src, cls in ADAPTERS.items()}
            for chain, chain_hotels in groups.items():
                random.shuffle(chain_hotels)
                consecutive_blocks = 0
                log.info("== %s (%d hotels)", chain, len(chain_hotels))
                for h in chain_hotels:
                    adapter = adapters[h.source]
                    adapter.warm_up(h)
                    for s in stays:
                        t0 = time.time()
                        if consecutive_blocks >= max_blocks:
                            result = ScrapeResult(SKIPPED, error=f"{chain} blocked {consecutive_blocks}x in a row this run")
                        else:
                            result = _scrape_with_retry(adapter, h, s)
                            consecutive_blocks = consecutive_blocks + 1 if result.status == BLOCKED else 0
                        # A sold-out answer is data too: it yields std rows with available=0.
                        std = standard_prices(h, result.offers, policy) \
                            if result.status in (OK, NO_AVAILABILITY) else []
                        raw_path = store.save_raw(today, h, s, result.raw)
                        store.save_job(run_id, today, h, s, result, std, time.time() - t0, raw_path)
                        stats[result.status] += 1
                        by_source[h.source][result.status] += 1
                        a = next((x for x in std if x.room_std == "A"), None)
                        a_txt = f"A=₹{a.offer.price_pre_tax:,.0f} ({a.offer.room_name})" if a and a.offer else ""
                        log.info("  %-28s %-8s %s %-15s %3d offers %s %s", h.id, s.horizon_label, s.checkin,
                                 result.status, len(result.offers), a_txt, result.error[:80])
                        if result.page_url:  # only pause after a real page load
                            time.sleep(random.uniform(lo, hi))
    finally:
        store.finish_run(run_id)
        store.close()

    summary = {"run_id": run_id, "scrape_date": today.isoformat(), "totals": dict(stats),
               "by_source": {k: dict(v) for k, v in by_source.items()}}
    _write_report(cfg, summary)
    return summary


def _scrape_with_retry(adapter, hotel: Hotel, stay, attempts: int = 2) -> ScrapeResult:
    result = ScrapeResult(ERROR, error="not attempted")
    for i in range(attempts):
        try:
            result = adapter.scrape(hotel, stay)
        except Exception as e:  # noqa: BLE001 - one bad page must not stop the run
            result = ScrapeResult(ERROR, error=f"{type(e).__name__}: {e}")
        if result.status != ERROR:
            return result
        if i + 1 < attempts:
            time.sleep(10)
    return result


def _write_report(cfg: Config, summary: dict) -> None:
    folder = cfg.path("report_dir")
    folder.mkdir(parents=True, exist_ok=True)
    lines = [f"Run {summary['run_id']}  scrape date {summary['scrape_date']}", ""]
    total = sum(summary["totals"].values()) or 1
    ok = summary["totals"].get(OK, 0) + summary["totals"].get(NO_AVAILABILITY, 0)
    lines.append(f"Success: {ok}/{total} jobs ({ok / total:.0%})")
    for src, counts in summary["by_source"].items():
        lines.append(f"  {src:10s} " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    text = "\n".join(lines)
    (folder / f"run_{summary['run_id']}.txt").write_text(text, encoding="utf-8")
    log.info("\n%s", text)
