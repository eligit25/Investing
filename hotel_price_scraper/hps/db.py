"""SQLite storage: one row per scrape job, per offer, and per standard price."""
from __future__ import annotations

import gzip
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from .config import Hotel
from .dates import Stay
from .models import ScrapeResult
from .rooms import StdPrice

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    scrape_date TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    host        TEXT,
    note        TEXT
);

-- One row per (hotel, check-in date) attempt. Reruns on the same day replace it.
CREATE TABLE IF NOT EXISTS jobs (
    scrape_date   TEXT NOT NULL,
    hotel_id      TEXT NOT NULL,
    source        TEXT NOT NULL,
    checkin_date  TEXT NOT NULL,
    run_id        TEXT NOT NULL,
    scraped_at    TEXT NOT NULL,
    horizon_label TEXT,
    status        TEXT NOT NULL,
    error         TEXT,
    n_offers      INTEGER,
    duration_s    REAL,
    page_url      TEXT,
    raw_path      TEXT,
    PRIMARY KEY (scrape_date, hotel_id, source, checkin_date)
);

-- Every room x rate the engine returned.
CREATE TABLE IF NOT EXISTS offers (
    scrape_date        TEXT NOT NULL,
    hotel_id           TEXT NOT NULL,
    source             TEXT NOT NULL,
    checkin_date       TEXT NOT NULL,
    checkout_date      TEXT NOT NULL,
    room_code          TEXT,
    room_name          TEXT,
    room_std           TEXT,
    rate_code          TEXT,
    rate_name          TEXT,
    is_member          INTEGER,
    is_refundable      INTEGER,
    breakfast_included INTEGER,
    price_pre_tax      REAL,
    price_incl_tax     REAL,
    price_list         REAL,
    currency           TEXT,
    rooms_left         INTEGER
);
CREATE INDEX IF NOT EXISTS offers_key ON offers (scrape_date, hotel_id, source, checkin_date);

-- The analysis table: one price per hotel x check-in x standard room per scrape day.
CREATE TABLE IF NOT EXISTS std_prices (
    scrape_date        TEXT NOT NULL,
    hotel_id           TEXT NOT NULL,
    source             TEXT NOT NULL,
    checkin_date       TEXT NOT NULL,
    room_std           TEXT NOT NULL,
    hotel_name         TEXT,
    micromarket        TEXT,
    company            TEXT,
    segment            TEXT,
    horizon_label      TEXT,
    horizon_days       INTEGER,
    lead_days          INTEGER,
    checkin_dow        TEXT,
    nights             INTEGER,
    method             TEXT,
    available          INTEGER NOT NULL,
    room_code          TEXT,
    room_name          TEXT,
    rate_code          TEXT,
    rate_name          TEXT,
    breakfast_included INTEGER,
    price_pre_tax      REAL,
    price_incl_tax     REAL,
    price_list         REAL,
    currency           TEXT,
    rooms_left         INTEGER,
    PRIMARY KEY (scrape_date, hotel_id, source, checkin_date, room_std)
);
"""


def _b(v):
    return None if v is None else int(bool(v))


class Store:
    def __init__(self, db_path: Path, raw_dir: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.raw_dir = raw_dir
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.close()

    def start_run(self, run_id: str, scrape_date: date, host: str, note: str = ""):
        self.conn.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)",
                          (run_id, scrape_date.isoformat(), datetime.now().isoformat(timespec="seconds"), None, host, note))
        self.conn.commit()

    def finish_run(self, run_id: str):
        self.conn.execute("UPDATE runs SET finished_at=? WHERE run_id=?",
                          (datetime.now().isoformat(timespec="seconds"), run_id))
        self.conn.commit()

    def save_raw(self, scrape_date: date, hotel: Hotel, stay: Stay, raw) -> str | None:
        if raw is None:
            return None
        folder = self.raw_dir / scrape_date.isoformat() / hotel.source
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{hotel.id}_{stay.checkin.isoformat()}.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(raw, f)
        return str(path)

    def save_job(self, run_id: str, scrape_date: date, hotel: Hotel, stay: Stay,
                 result: ScrapeResult, std: list[StdPrice], duration_s: float, raw_path: str | None):
        sd, ci = scrape_date.isoformat(), stay.checkin.isoformat()
        key = (sd, hotel.id, hotel.source, ci)
        c = self.conn
        c.execute("DELETE FROM offers WHERE scrape_date=? AND hotel_id=? AND source=? AND checkin_date=?", key)
        c.execute("DELETE FROM std_prices WHERE scrape_date=? AND hotel_id=? AND source=? AND checkin_date=?", key)
        c.execute("INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (*key, run_id, datetime.now().isoformat(timespec="seconds"), stay.horizon_label,
                   result.status, result.error[:500] if result.error else None, len(result.offers),
                   round(duration_s, 1), result.page_url, raw_path))
        c.executemany(
            "INSERT INTO offers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(*key, stay.checkout.isoformat(), o.room_code, o.room_name, o.room_std, o.rate_code, o.rate_name,
              _b(o.is_member), _b(o.is_refundable), _b(o.breakfast_included), o.price_pre_tax, o.price_incl_tax,
              o.price_list, o.currency, o.rooms_left) for o in result.offers])
        if result.status in ("ok", "no_availability"):
            nights = (stay.checkout - stay.checkin).days
            lead = (stay.checkin - scrape_date).days
            dow = stay.checkin.strftime("%a").lower()
            for s in std:
                o = s.offer
                c.execute(
                    "INSERT OR REPLACE INTO std_prices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (*key, s.room_std, hotel.name, hotel.micromarket, hotel.company, hotel.segment,
                     stay.horizon_label, stay.horizon_days, lead, dow, nights, s.method, int(o is not None),
                     o.room_code if o else None, o.room_name if o else None, o.rate_code if o else None,
                     o.rate_name if o else None, _b(o.breakfast_included) if o else None,
                     o.price_pre_tax if o else None, o.price_incl_tax if o else None,
                     o.price_list if o else None, o.currency if o else None, o.rooms_left if o else None))
        c.commit()
