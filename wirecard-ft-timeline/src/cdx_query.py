"""Wayback CDX query for FT Wirecard coverage.

Pulls candidate URLs from three patterns, writes raw JSON per pattern to
data/raw/, and an interim deduped candidate list to data/interim/.

No filtering beyond statuscode:200 + collapse=urlkey here. Further triage
(headline extraction, paywall detection, dedup by (headline, date)) happens
downstream.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
INTERIM = ROOT / "data" / "interim"

CDX = "https://web.archive.org/cdx/search/cdx"
UA = "wirecard-ft-timeline-research/0.1 (+academic; contact via repo)"

PATTERNS = {
    "ft_wirecard_prefix": "ft.com/wirecard*",
    "ft_content_wirecard": "ft.com/content/*wirecard*",
    "alphaville_wirecard": "ftalphaville.ft.com/*wirecard*",
}


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=2, min=2, max=16))
def cdx_fetch(url_pattern: str) -> list[list[str]]:
    params = {
        "url": url_pattern,
        "output": "json",
        "from": "2015",
        "to": "2020",
        "filter": "statuscode:200",
        "collapse": "urlkey",
    }
    r = requests.get(CDX, params=params, timeout=60, headers={"User-Agent": UA})
    r.raise_for_status()
    return r.json()


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    INTERIM.mkdir(parents=True, exist_ok=True)
    retrieval_ts = datetime.now(timezone.utc).isoformat()

    all_rows: list[dict] = []
    for name, pattern in PATTERNS.items():
        print(f"[{name}] querying pattern: {pattern}", file=sys.stderr)
        rows = cdx_fetch(pattern)
        out = RAW / f"cdx_{name}.json"
        out.write_text(json.dumps(rows, indent=2))
        header, *data = rows if rows else [[], []]
        print(f"[{name}] rows={len(data)} written to {out.name}", file=sys.stderr)
        for r in data:
            record = dict(zip(header, r))
            record["_pattern"] = name
            record["_retrieval_ts"] = retrieval_ts
            all_rows.append(record)
        time.sleep(1)

    combined = INTERIM / "cdx_all_candidates.json"
    combined.write_text(json.dumps(all_rows, indent=2))
    print(f"combined total: {len(all_rows)} rows -> {combined}", file=sys.stderr)

    print("\n--- FIRST 20 ROWS (pre-dedup, pre-filter) ---")
    for i, r in enumerate(all_rows[:20], 1):
        print(
            f"{i:2d}. [{r['_pattern']}] ts={r.get('timestamp')} "
            f"status={r.get('statuscode')} mime={r.get('mimetype')}\n"
            f"    {r.get('original')}"
        )


if __name__ == "__main__":
    main()
