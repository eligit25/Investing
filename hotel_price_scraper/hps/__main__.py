"""Command line:  python -m hps <command> ...   (run from the hotel_price_scraper folder)

  run          daily scrape of all verified hotels
  test-site    open real booking pages for one chain and write a probe report
  rooms        list a hotel's rooms and suggest the A/B mapping
  discover     find hotel IDs (SynXis chains) or property codes (Lemon Tree)
  learn-url    turn a booking URL copied from your browser into a hotel's URL template
  export       write the Excel/CSV outputs
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date

from .config import load_config


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m hps", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="daily scrape")
    r.add_argument("--source", action="append", help="only these sources (synxis, reztrip, taj, marriott, hyatt)")
    r.add_argument("--hotel", action="append", help="only these hotel ids (also runs unverified ones)")
    r.add_argument("--micromarket", action="append")
    r.add_argument("--include-unverified", action="store_true", help="include hotels marked verified: false")
    r.add_argument("--max-dates", type=int, help="only the first N check-in dates (quick tests)")
    r.add_argument("--date", type=date.fromisoformat, help="pretend today is this date (YYYY-MM-DD)")
    r.add_argument("--headed", action="store_true", help="show the browser window")
    r.add_argument("--dry-run", action="store_true", help="print the schedule and URLs only")
    r.add_argument("--export", action="store_true", help="write the Excel file afterwards")

    t = sub.add_parser("test-site", help="probe one chain's website from this computer")
    t.add_argument("source", choices=["synxis", "reztrip", "taj", "marriott", "hyatt"])
    t.add_argument("--hotel", action="append")
    t.add_argument("--headless", action="store_true", help="hide the browser (default: visible)")

    rm = sub.add_parser("rooms", help="list rooms and suggest A/B rules for a hotel")
    rm.add_argument("hotel_id")
    rm.add_argument("--headed", action="store_true")

    d = sub.add_parser("discover", help="find hotel IDs / codes")
    d.add_argument("kind", choices=["synxis", "lemontree"])
    d.add_argument("args", nargs="+",
                   help="synxis: <base_url> <chain_id>   lemontree: <city page URL>... "
                        "(e.g. https://www.lemontreehotels.com/mumbai-hotels)")
    d.add_argument("--headed", action="store_true")

    lu = sub.add_parser("learn-url", help="store a booking URL as a hotel's template")
    lu.add_argument("hotel_id")
    lu.add_argument("url")
    lu.add_argument("checkin")
    lu.add_argument("checkout")

    sub.add_parser("export", help="write Excel/CSV from the database")

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    cfg = load_config()

    if a.cmd == "run":
        from .run import run, select_hotels
        hotels = select_hotels(cfg, a.source, a.hotel, a.micromarket, a.include_unverified)
        if not hotels:
            print("no hotels selected (unverified hotels need --include-unverified or --hotel)")
            return 1
        summary = run(cfg, hotels, today=a.date, headless=False if a.headed else None,
                      max_dates=a.max_dates, dry_run=a.dry_run)
        if a.export and summary:
            from .export import export_excel
            print("wrote", export_excel(cfg))
    elif a.cmd == "test-site":
        from .tools import test_site
        test_site(cfg, a.source, a.hotel, headed=not a.headless)
    elif a.cmd == "rooms":
        from .tools import suggest_rooms
        print(suggest_rooms(cfg, cfg.hotel(a.hotel_id), headed=a.headed))
    elif a.cmd == "discover":
        from .tools import discover_lemontree, discover_synxis
        if a.kind == "synxis":
            if len(a.args) != 2:
                p.error("discover synxis needs <base_url> <chain_id>")
            rows = discover_synxis(a.args[0], a.args[1], a.headed, cfg.settings)
        else:
            rows = discover_lemontree(a.args, a.headed, cfg.settings)
        print(json.dumps(rows, indent=1, ensure_ascii=False))
    elif a.cmd == "learn-url":
        from .tools import learn_url
        print("stored template:", learn_url(cfg, a.hotel_id, a.url, a.checkin, a.checkout))
    elif a.cmd == "export":
        from .export import export_excel
        print("wrote", export_excel(cfg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
