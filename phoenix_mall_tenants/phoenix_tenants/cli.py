"""Command line entry point: python -m phoenix_tenants <command> ..."""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .http import Fetcher
from .malls import MALLS, get_malls
from .platforms import FIELDS, scrape_current


def _write(rows: list[dict], path: Path, fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or (list(rows[0].keys()) if rows else ["empty"])
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _wayback_fetcher() -> Fetcher:
    # The Wayback Machine rate-limits aggressively (HTTP 429, then temporary IP blocks).
    return Fetcher(min_interval=1.5, retries=5, timeout=120)


def cmd_current(a) -> None:
    f = Fetcher(min_interval=a.delay)
    as_of = datetime.now(timezone.utc).date().isoformat()
    rows, failed = [], []
    for m in get_malls(a.malls):
        try:
            got = scrape_current(f, m, as_of)
        except Exception as e:
            failed.append(m.mall_id)
            print(f"{m.mall_id:26s} FAILED: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        rows += got
        n_dine = sum(r["tenant_type"] == "dine" for r in got)
        print(f"{m.mall_id:26s} {len(got):4d} tenants ({n_dine} dining)")
    out = Path(a.out) / "current" / as_of / "tenants.csv"
    _write(rows, out, FIELDS)
    print(f"\nWrote {len(rows)} rows -> {out}")
    if failed:
        print(f"Failed: {failed}", file=sys.stderr)
        sys.exit(1)


def cmd_wayback_discover(a) -> None:
    from .wayback import discover
    f = _wayback_fetcher()
    hosts = a.hosts or [h for m in get_malls(a.malls) for h in (m.legacy_hosts or (m.root.split("://")[1],))]
    rows = []
    for h in hosts:
        rows += discover(f, h)
    out = Path(a.out) / "wayback" / "discover.csv"
    _write(rows, out)
    print(f"Wrote {len(rows)} candidate directory URLs -> {out}")


def cmd_wayback_listings(a) -> None:
    from .wayback import listing_history
    f = _wayback_fetcher()
    cache = Path(a.out) / "cache" / "wayback"
    for m in get_malls(a.malls):
        print(m.mall_id)
        rows, log = listing_history(f, m, cache, freq=a.freq, from_=a.since, to=a.until,
                                    extra_urls=a.listing_url or (), extra_prefixes=a.detail_prefix or ())
        base = Path(a.out) / "wayback" / "listings"
        _write(rows, base / f"{m.mall_id}.csv", FIELDS + ["snapshot_ts", "snapshot_url"])
        _write(log, base / f"{m.mall_id}_snapshots.csv",
               ["mall_id", "url", "timestamp", "status", "n_tenants"])
        ok = sum(1 for x in log if x["status"].startswith("ok"))
        shells = sum(1 for x in log if x["status"] == "empty_shell")
        print(f"  -> {len(rows)} tenant observations from {ok} snapshots "
              f"({shells} JS-only shells with no tenant list)")


def cmd_wayback_details(a) -> None:
    from .wayback import detail_inventory
    f = _wayback_fetcher()
    for m in get_malls(a.malls):
        print(m.mall_id)
        rows = detail_inventory(f, m, from_=a.since, to=a.until, extra_prefixes=a.detail_prefix or ())
        out = Path(a.out) / "wayback" / "details" / f"{m.mall_id}.csv"
        _write(rows, out)
        print(f"  -> {len(rows)} tenants with archived detail pages -> {out}")


def cmd_history(a) -> None:
    from .history import changes, load_observations, reconcile_names
    data = Path(a.out)
    df = load_observations(data)
    if a.malls:
        df = df[df["mall_id"].isin(a.malls)]
    df = reconcile_names(df)
    events, summary = changes(df, confirm=a.confirm)
    out = data / "history"
    out.mkdir(parents=True, exist_ok=True)
    events.to_csv(out / "events.csv", index=False)
    summary.to_csv(out / "summary.csv", index=False)
    df.to_csv(out / "observations.csv", index=False)
    with pd.option_context("display.width", 200, "display.max_columns", 12):
        cols = [c for c in ["family", "mall_id", "as_of", "n_tenants", "n_open", "n_close", "churn_pct",
                            "luxury_premium_pct"] if c in summary.columns]
        print(summary[cols].to_string(index=False))
    print(f"\nWrote {out}/summary.csv, events.csv, observations.csv")


def cmd_diff(a) -> None:
    from .history import diff_files
    d = diff_files(Path(a.before), Path(a.after))
    if d.empty:
        print("No changes.")
        return
    print(d.to_string(index=False))
    if a.save:
        d.to_csv(a.save, index=False)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="phoenix_tenants", description=__doc__)
    p.add_argument("--out", default="data", help="data directory (default: ./data)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def malls_arg(sp):
        sp.add_argument("--malls", nargs="*", metavar="ID", help=f"subset of: {', '.join(MALLS)}")

    sp = sub.add_parser("list", help="show the mall registry")
    sp.set_defaults(func=lambda a: [print(f"{m.mall_id:26s} {m.platform:12s} {m.base}") for m in get_malls()])

    sp = sub.add_parser("current", help="scrape today's tenant lists from the live sites")
    malls_arg(sp)
    sp.add_argument("--delay", type=float, default=1.0, help="seconds between requests per host")
    sp.set_defaults(func=cmd_current)

    sp = sub.add_parser("wayback-discover", help="list archived directory-like URLs on (legacy) hosts")
    malls_arg(sp)
    sp.add_argument("--hosts", nargs="*", help="explicit hosts/prefixes, e.g. highstreetphoenix.com")
    sp.set_defaults(func=cmd_wayback_discover)

    for name, fn, hlp in (("wayback-listings", cmd_wayback_listings, "periodic archived directory snapshots"),
                          ("wayback-details", cmd_wayback_details, "first/last-seen inventory of tenant pages")):
        sp = sub.add_parser(name, help=hlp)
        malls_arg(sp)
        sp.add_argument("--since", help="YYYY or YYYYMMDD")
        sp.add_argument("--until", help="YYYY or YYYYMMDD")
        sp.add_argument("--detail-prefix", action="append",
                        help="extra detail-page URL prefix (repeatable), e.g. highstreetphoenix.com/store/")
        if name == "wayback-listings":
            sp.add_argument("--freq", choices=["M", "Q", "Y"], default="Q")
            sp.add_argument("--listing-url", action="append", help="extra archived listing URL (repeatable)")
        sp.set_defaults(func=fn)

    sp = sub.add_parser("history", help="build change analytics from all observations")
    malls_arg(sp)
    sp.add_argument("--confirm", type=int, default=1,
                    help="snapshots a tenant must be missing from before counting a closure")
    sp.set_defaults(func=cmd_history)

    sp = sub.add_parser("diff", help="openings/closures between two tenants.csv files")
    sp.add_argument("before")
    sp.add_argument("after")
    sp.add_argument("--save")
    sp.set_defaults(func=cmd_diff)

    a = p.parse_args(argv)
    a.func(a)
