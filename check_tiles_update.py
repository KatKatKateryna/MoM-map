#!/usr/bin/env python3
"""
Quick check, before the slow environment setup in CI, that update_tiles.py has work to do.

Lists the CSVs on the MoM output server (MOM_CSV_URL) and compares the newest window
(same settings as update_tiles.py, from tiles_listing.py) with the snapshots already in
data/tiles/metadata.json. Exits 1 within seconds when the server cannot be reached, lists
no CSV, or there is no new snapshot to make (update_tiles.py would end the same way, after
minutes of setup); 0 when there is work, or with OVERWRITE_EXISTING (a full rebuild).
Standard library only.

Usage:
    python check_tiles_update.py
"""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from tiles_listing import (
    CSV_HREF_RE,
    EFFECTIVE_MAX_SNAPSHOTS,
    ONLY_TIMESTAMP_PER_DAY,
    OVERWRITE_EXISTING,
    keep_latest_per_day,
    timestamp_sort_key,
)

OUT_DIR = Path(__file__).parent.resolve() / "data" / "tiles"
ATTEMPTS = 3
TIMEOUT = 15  # seconds per attempt


def fetch_listing(url):
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            print(f"  listing attempt {attempt}/{ATTEMPTS} failed: {e}")
            if attempt < ATTEMPTS:
                time.sleep(2 * attempt)
    return None


def main():
    url = os.getenv("MOM_CSV_URL")
    if not url:
        print("ERROR: MOM_CSV_URL environment variable is not set.")
        return 1
    if OVERWRITE_EXISTING:
        print("OVERWRITE_EXISTING set: full rebuild, no check.")
        return 0

    html = fetch_listing(url)
    if html is None:
        print("ERROR: MoM output server unreachable.")
        return 1
    names = sorted(set(CSV_HREF_RE.findall(html)), key=timestamp_sort_key, reverse=True)
    if not names:
        print("ERROR: no CSV found on the MoM output server.")
        return 1
    if ONLY_TIMESTAMP_PER_DAY:
        names = keep_latest_per_day(names, lambda n: n, label="/check")
    window = names[:EFFECTIVE_MAX_SNAPSHOTS]

    # Same rule as update_tiles.py: a snapshot counts only if its tile file exists
    try:
        snapshots = json.loads((OUT_DIR / "metadata.json").read_text()).get("snapshots", [])
    except (OSError, ValueError):
        snapshots = []
    have = {s["csv"] for s in snapshots if s.get("csv") and (OUT_DIR / s.get("file", "")).exists()}
    missing = [n for n in window if n not in have]
    if not missing:
        print(f"No update (latest: {names[0]}); nothing to do.")
        return 1
    print(f"{len(missing)} new snapshot(s) to make: {missing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
