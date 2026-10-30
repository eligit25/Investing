"""Load and validate the YAML configuration."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

SOURCES = {"synxis", "reztrip", "taj", "marriott", "hyatt"}


@dataclass
class RoomRule:
    codes: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)  # regexes, case-insensitive


@dataclass
class Hotel:
    id: str
    name: str
    micromarket: str
    company: str
    source: str
    params: dict[str, Any]
    brand: str = ""
    segment: str = ""
    rooms: dict[str, RoomRule] = field(default_factory=dict)  # "A"/"B" -> rule
    verified: bool = True
    enabled: bool = True

    @property
    def chain(self) -> str:
        """Label used to group hotels sharing a website (for pacing/blocks)."""
        return self.params.get("base_url") or self.source


@dataclass
class Micromarket:
    id: str
    name: str
    city: str
    notes: str = ""


@dataclass
class Config:
    settings: dict[str, Any]
    micromarkets: dict[str, Micromarket]
    hotels: list[Hotel]

    def path(self, key: str) -> Path:
        p = Path(self.settings["storage"][key])
        return p if p.is_absolute() else ROOT / p

    def hotel(self, hotel_id: str) -> Hotel:
        for h in self.hotels:
            if h.id == hotel_id:
                return h
        raise KeyError(f"unknown hotel id: {hotel_id}")


def _load_yaml(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_config(config_dir: Path | None = None) -> Config:
    config_dir = config_dir or CONFIG_DIR
    settings = _load_yaml(config_dir / "settings.yaml")
    mm_raw = _load_yaml(config_dir / "micromarkets.yaml")["micromarkets"]
    micromarkets = {m["id"]: Micromarket(**m) for m in mm_raw}

    # URL templates learned with `python -m hps learn-url` override params.url_template.
    tpl_path = config_dir / "url_templates.yaml"
    templates = (_load_yaml(tpl_path) or {}) if tpl_path.exists() else {}

    hotels = []
    seen = set()
    for raw in _load_yaml(config_dir / "hotels.yaml")["hotels"]:
        raw = dict(raw)
        if raw["id"] in templates:
            raw["params"] = {**raw.get("params", {}), "url_template": templates[raw["id"]]}
        rooms = {
            k: RoomRule(codes=[str(c) for c in (v or {}).get("codes", [])],
                        names=list((v or {}).get("names", [])))
            for k, v in (raw.pop("rooms", None) or {}).items()
        }
        h = Hotel(rooms=rooms, **raw)
        if h.id in seen:
            raise ValueError(f"duplicate hotel id {h.id}")
        if h.source not in SOURCES:
            raise ValueError(f"{h.id}: unknown source {h.source!r} (expected one of {sorted(SOURCES)})")
        if h.micromarket not in micromarkets:
            raise ValueError(f"{h.id}: unknown micromarket {h.micromarket!r}")
        seen.add(h.id)
        hotels.append(h)
    return Config(settings=settings, micromarkets=micromarkets, hotels=hotels)
