"""Excel / CSV outputs built from the SQLite store."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from .config import Config


def export_excel(cfg: Config, out: Path | None = None) -> Path:
    with sqlite3.connect(cfg.path("db_path")) as conn:
        std = pd.read_sql("SELECT * FROM std_prices", conn)
        jobs = pd.read_sql("SELECT * FROM jobs", conn)
    if std.empty:
        raise SystemExit("no prices in the database yet - run `python -m hps run` first")
    latest = std["scrape_date"].max()
    out = out or cfg.path("export_dir") / f"hotel_prices_{latest}.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)

    today = std[std["scrape_date"] == latest]
    snapshot = today.pivot_table(index=["micromarket", "hotel_name", "room_std"], columns="horizon_label",
                                 values="price_pre_tax", aggfunc="first")
    snapshot = snapshot[sorted(snapshot.columns, key=_horizon_sort)]

    # Micromarket index: average room-A pre-tax price per scrape date and horizon.
    a = std[(std["room_std"] == "A") & (std["available"] == 1)]
    mm_index = a.pivot_table(index=["scrape_date"], columns=["micromarket", "horizon_label"],
                             values="price_pre_tax", aggfunc="mean").round(0)

    # Sell-out share: how many standard rooms were not on sale.
    sold_out = std[std["room_std"].isin(["A", "B"])].assign(sold_out=lambda d: 1 - d["available"]) \
        .pivot_table(index="scrape_date", columns="micromarket", values="sold_out", aggfunc="mean").round(3)

    # Booking curves: how the room-A price for one stay night moves as it approaches.
    curves = a.pivot_table(index=["micromarket", "hotel_name", "checkin_date"], columns="lead_days",
                           values="price_pre_tax", aggfunc="first")
    curves = curves[sorted(curves.columns, reverse=True)]

    health = jobs.pivot_table(index=["scrape_date", "source"], columns="status", values="hotel_id",
                              aggfunc="count", fill_value=0)

    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        snapshot.to_excel(xw, sheet_name=f"Snapshot {latest}")
        mm_index.to_excel(xw, sheet_name="Micromarket index (A)")
        sold_out.to_excel(xw, sheet_name="Sold-out share")
        curves.to_excel(xw, sheet_name="Booking curves (A)")
        std.sort_values(["scrape_date", "micromarket", "hotel_id", "checkin_date", "room_std"]) \
            .to_excel(xw, sheet_name="All standard prices", index=False)
        health.to_excel(xw, sheet_name="Run health")
    std.to_csv(out.with_suffix(".csv"), index=False)
    return out


def _horizon_sort(label: str):
    order = {"1w": 0, "1m": 1, "3m": 2, "6m": 3}
    base, _, anchor = label.partition("_")
    return (order.get(base, 99), base, anchor)
