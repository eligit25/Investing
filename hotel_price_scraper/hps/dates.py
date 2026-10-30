"""Turn forward horizons into concrete check-in dates for a scrape day."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

DOW = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
DOW_NAMES = {v: k for k, v in DOW.items()}


@dataclass(frozen=True)
class Stay:
    checkin: date
    checkout: date
    horizon_label: str   # e.g. "1m" or "1m_tue"
    horizon_days: int    # nominal horizon from settings
    anchor: str | None   # anchor weekday, or None for fixed offsets


def next_weekday_on_or_after(d: date, weekday: int) -> date:
    return d + timedelta(days=(weekday - d.weekday()) % 7)


def build_schedule(today: date, settings: dict) -> list[Stay]:
    nights = int(settings["stay"]["nights"])
    mode = settings.get("checkin_mode", "dow_anchored")
    anchors = [a.lower()[:3] for a in settings.get("anchor_days", ["tue", "sat"])]
    stays: list[Stay] = []
    seen: set[date] = set()
    for h in settings["horizons"]:
        every = int(h.get("every_n_days", 1))
        if every > 1 and today.toordinal() % every != 0:
            continue
        base = today + timedelta(days=int(h["days"]))
        if mode == "fixed_offset":
            targets = [(base, None)]
        elif mode == "dow_anchored":
            targets = [(next_weekday_on_or_after(base, DOW[a]), a) for a in anchors]
        else:
            raise ValueError(f"unknown checkin_mode {mode!r}")
        for checkin, anchor in targets:
            if checkin in seen:
                continue
            seen.add(checkin)
            label = h["label"] if anchor is None else f"{h['label']}_{anchor}"
            stays.append(Stay(checkin, checkin + timedelta(days=nights), label, int(h["days"]), anchor))
    return stays
