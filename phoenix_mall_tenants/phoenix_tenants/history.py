"""Turn tenant observations (live snapshots + archived listings) into change analytics.

Outputs per mall and snapshot date:
  * presence matrix   tenant x date -> listed?
  * events            openings / closures between consecutive snapshots
  * summary           tenant count, openings, closures, churn %, category mix, tier mix

Two source families are analysed separately, never compared with each other:
  * `listing`  complete directory snapshots (live scrapes + archived listing pages / feeds)
  * `details`  quarterly presence derived from Wayback detail-page first/last-seen windows

Within a family, observations from different sources are not equally complete (an old site might list only
retail, a JSON feed only dining). Comparisons between two snapshots therefore only use the
tenant types present in both. A `confirm` setting requires a tenant to be missing from N
consecutive snapshots before a closure is recorded, so one patchy capture doesn't create
fake churn.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

from .normalize import load_tiers

OBS_COLS = ["mall_id", "mall_name", "as_of", "tenant_type", "name", "name_key", "category", "source"]


def details_to_panel(details: pd.DataFrame, freq: str = "Q", still_open_within_days: int = 365) -> pd.DataFrame:
    """Expand detail-page windows into one observation per tenant per period end.

    A tenant counts as present in every period between its first and last successful
    capture. Crawls are sparse, so a tenant whose page was still returning 200 within
    `still_open_within_days` of the mall's most recent capture is treated as present up to
    that capture, rather than "closing" just because the crawler hasn't come back yet.
    """
    if details.empty:
        return pd.DataFrame(columns=OBS_COLS)
    d = details.copy()
    for c in ("first_seen", "last_seen", "last_capture"):
        d[c] = pd.to_datetime(d[c])
    exited = d["likely_exited"].astype(str).str.lower() == "true"
    rows = []
    for mall_id, g in d.groupby("mall_id"):
        horizon = g["last_capture"].max()
        ends = g["last_seen"].where(exited.loc[g.index] |
                                    (g["last_seen"] < horizon - pd.Timedelta(days=still_open_within_days)),
                                    horizon)
        for (_, r), end in zip(g.iterrows(), ends):
            for p in pd.period_range(r["first_seen"], end, freq=freq):
                rows.append({"mall_id": mall_id, "mall_name": r.get("mall_name", ""),
                             "as_of": p.end_time.date().isoformat(), "tenant_type": r["tenant_type"],
                             "name": r["name"], "name_key": r["name_key"], "category": r["category"],
                             "source": "wayback:details"})
    return pd.DataFrame(rows, columns=OBS_COLS)


def load_observations(data_dir: Path) -> pd.DataFrame:
    frames = []
    for p in sorted((data_dir / "current").glob("*/tenants.csv")):
        frames.append(pd.read_csv(p, dtype=str))
    for p in sorted((data_dir / "wayback" / "listings").glob("*.csv")):
        if not p.name.endswith("_snapshots.csv"):
            frames.append(pd.read_csv(p, dtype=str))
    for p in sorted((data_dir / "wayback" / "details").glob("*.csv")):
        frames.append(details_to_panel(pd.read_csv(p, dtype=str)).astype(str))
    frames = [x for x in frames if not x.empty]
    if not frames:
        raise SystemExit(f"No observations under {data_dir}. Run `current` and/or a `wayback-*` command first.")
    df = pd.concat(frames, ignore_index=True)
    df = df[[c for c in OBS_COLS if c in df.columns]].dropna(subset=["name_key"])
    df = df[df["name_key"].str.len() > 0]
    df["family"] = df["source"].map(lambda s: "details" if s == "wayback:details" else "listing")
    # Several archived captures can land on the same day; one snapshot per mall-day.
    return df.drop_duplicates(["family", "mall_id", "as_of", "tenant_type", "name_key"])


def same_brand(a: str, b: str, token_ratio: float = 0.8) -> bool:
    """Spelling variants only: 'milk bar'~'milkbar', 'van huesen'~'van heusen',
    'louis phillipe'~'louis philippe'. Never merges on a whole-word difference, so
    'van heusen men' vs 'van heusen women' or 'adidas' vs 'adidas originals' stay apart.
    """
    if a == b or a.replace(" ", "") == b.replace(" ", ""):
        return True
    ta, tb = a.split(), b.split()
    if len(ta) != len(tb) or a[:1] != b[:1]:
        return False
    for x, y in zip(ta, tb):
        if x == y:
            continue
        if min(len(x), len(y)) < 4 or SequenceMatcher(None, x, y).ratio() < token_ratio:
            return False
    return True


def reconcile_names(df: pd.DataFrame) -> pd.DataFrame:
    """Merge spelling variants of the same brand within a mall ('Adiddas' -> 'adidas')."""
    df = df.copy()
    for mall_id, g in df.groupby("mall_id"):
        counts = g["name_key"].value_counts()
        canon: dict[str, str] = {}
        keys = list(counts.index)      # most frequent spelling first, so it wins
        for i, k in enumerate(keys):
            if k in canon:
                continue
            canon[k] = k
            for k2 in keys[i + 1:]:
                if k2 not in canon and same_brand(k, k2):
                    canon[k2] = k
        m = df["mall_id"] == mall_id
        df.loc[m, "name_key"] = df.loc[m, "name_key"].map(canon)
    return df


def presence(df: pd.DataFrame, mall_id: str) -> pd.DataFrame:
    g = df[df["mall_id"] == mall_id]
    p = (g.assign(v=1).pivot_table(index=["tenant_type", "name_key"], columns="as_of", values="v",
                                   aggfunc="max", fill_value=0))
    return p.sort_index(axis=1)


def changes(df: pd.DataFrame, confirm: int = 1) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (events, summary) across all malls."""
    tiers = load_tiers()
    events, summary = [], []
    if "family" not in df.columns:
        df = df.assign(family="listing")
    for (family, mall_id), g in df.groupby(["family", "mall_id"]):
        dates = sorted(g["as_of"].unique())
        snaps = {d: g[g["as_of"] == d] for d in dates}
        types = {d: set(s["tenant_type"]) for d, s in snaps.items()}
        keys = {d: set(zip(s["tenant_type"], s["name_key"])) for d, s in snaps.items()}
        names = dict(zip(zip(g["tenant_type"], g["name_key"]), g["name"]))
        cats = dict(zip(zip(g["tenant_type"], g["name_key"]), g["category"]))
        for i, d in enumerate(dates):
            s = snaps[d]
            row = {"family": family, "mall_id": mall_id, "as_of": d, "n_tenants": len(keys[d]),
                   "tenant_types": ",".join(sorted(types[d])), "sources": ",".join(sorted(set(s["source"])))}
            if i > 0:
                prev = dates[i - 1]
                common = types[prev] & types[d]
                before = {k for k in keys[prev] if k[0] in common}
                after = {k for k in keys[d] if k[0] in common}
                opened = after - before
                closed = set()
                for k in before - after:
                    # Confirm: still missing in the next confirm-1 snapshots that cover this type.
                    later = [x for x in dates[i + 1:] if k[0] in types[x]][:confirm - 1]
                    if len(later) == confirm - 1 and all(k not in keys[x] for x in later):
                        closed.add(k)
                base = max((len(before) + len(after)) / 2, 1)
                row.update({"compared_with": prev, "n_open": len(opened), "n_close": len(closed),
                            "churn_pct": round(100 * (len(opened) + len(closed)) / 2 / base, 1)})
                for kind, ks in (("open", opened), ("close", closed)):
                    for k in sorted(ks):
                        events.append({"family": family, "mall_id": mall_id, "as_of": d, "since": prev, "event": kind,
                                       "tenant_type": k[0], "name": names.get(k, k[1]), "name_key": k[1],
                                       "category": cats.get(k, ""), "tier": tiers.get(k[1], "")})
            mix = s["category"].value_counts(normalize=True)
            for c, v in mix.items():
                row[f"cat:{c}"] = round(100 * v, 1)
            tiered = s["name_key"].map(tiers).fillna("")
            row["luxury_premium_pct"] = round(100 * tiered.isin(["luxury", "premium"]).mean(), 1)
            summary.append(row)
    ev = pd.DataFrame(events)
    sm = pd.DataFrame(summary)
    lead = ["family", "mall_id", "as_of", "compared_with", "n_tenants", "n_open", "n_close", "churn_pct",
            "luxury_premium_pct", "tenant_types", "sources"]
    if not sm.empty:
        sm = sm[[c for c in lead if c in sm.columns] + sorted(c for c in sm.columns if c not in lead)]
    return ev, sm


def diff_files(a: Path, b: Path) -> pd.DataFrame:
    """Openings/closures between two tenant CSVs (e.g. two `current` runs)."""
    da, db = pd.read_csv(a, dtype=str), pd.read_csv(b, dtype=str)
    ka = set(zip(da["mall_id"], da["tenant_type"], da["name_key"]))
    kb = set(zip(db["mall_id"], db["tenant_type"], db["name_key"]))
    names = {**dict(zip(zip(da["mall_id"], da["tenant_type"], da["name_key"]), da["name"])),
             **dict(zip(zip(db["mall_id"], db["tenant_type"], db["name_key"]), db["name"]))}
    rows = [{"mall_id": k[0], "event": "open", "tenant_type": k[1], "name": names[k]} for k in kb - ka]
    rows += [{"mall_id": k[0], "event": "close", "tenant_type": k[1], "name": names[k]} for k in ka - kb]
    return pd.DataFrame(rows).sort_values(["mall_id", "event", "name"]) if rows else pd.DataFrame(rows)
