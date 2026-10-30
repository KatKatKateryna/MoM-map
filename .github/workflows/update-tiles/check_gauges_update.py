#!/usr/bin/env python3
"""
Quick check, before the slow environment setup in CI, that update_gauges.py has work to do.

Lists the GloFAS gauge files (threspoints_*.csv) in the GLOFAS folder next to MOM_CSV_URL's
Final_Alert and compares the newest RETENTION_DAYS of them with the gauge snapshots already
made: those in data/tiles/gauges/metadata.json, or with GAUGES_TILES_URL (the deployed
data/tiles/gauges/ folder) those in its metadata.json whose file is there.
Never fails: writes new=true or new=false to $GITHUB_OUTPUT (and prints it), so the later
steps are skipped, not failed, when the server is unreachable or there is nothing new.
OVERWRITE_EXISTING (a full rebuild) always gives new=true.
Standard library only.

Usage:
    python check_gauges_update.py
"""

import json
import os
import urllib.request
from pathlib import Path

from check_tiles_update import TIMEOUT, fetch_listing
from tiles_listing import (
    GAUGE_HREF_RE,
    OVERWRITE_EXISTING,
    RETENTION_DAYS,
    gauge_sort_key,
    gauges_url,
)

# Repo root (this file is in .github/workflows/update-tiles/)
OUT_DIR = Path(__file__).resolve().parents[3] / "data" / "tiles" / "gauges"


def deployed_snapshots(base, wanted):
    """Snapshots of the files in wanted in the deployed metadata.json whose tile file is there too."""
    base = base.rstrip("/") + "/"
    try:
        with urllib.request.urlopen(base + "metadata.json", timeout=TIMEOUT) as r:
            snapshots = json.loads(r.read()).get("snapshots", [])
    except Exception as e:
        print(f"  no deployed gauges metadata.json ({e}): every file counts as new")
        return []
    kept = []
    for s in snapshots:
        if s.get("csv") not in wanted:
            continue
        try:
            req = urllib.request.Request(base + s.get("file", ""), method="HEAD")
            urllib.request.urlopen(req, timeout=TIMEOUT).close()
            kept.append(s)
        except Exception:
            print(f"  {s.get('file')} listed but missing: counts as new")
    return kept


def has_new():
    url = gauges_url(os.getenv("MOM_CSV_URL"))
    if not url:
        print("MOM_CSV_URL is not set or has no /Final_Alert: no gauges URL, skipping.")
        return False
    if OVERWRITE_EXISTING:
        print("OVERWRITE_EXISTING set: full rebuild, no check.")
        return True

    html = fetch_listing(url)
    if html is None:
        print("GLOFAS folder unreachable: skipping gauges.")
        return False
    names = sorted(set(GAUGE_HREF_RE.findall(html)), key=gauge_sort_key, reverse=True)
    if not names:
        print("No gauge file in the GLOFAS folder: skipping gauges.")
        return False
    window = names[:RETENTION_DAYS]

    # Same rule as update_gauges.py: a snapshot counts only if its tile file exists
    if os.getenv("GAUGES_TILES_URL"):
        have = {s["csv"] for s in deployed_snapshots(os.environ["GAUGES_TILES_URL"], set(window)) if s.get("csv")}
    else:
        try:
            snapshots = json.loads((OUT_DIR / "metadata.json").read_text()).get("snapshots", [])
        except (OSError, ValueError):
            snapshots = []
        have = {s["csv"] for s in snapshots if s.get("csv") and (OUT_DIR / s.get("file", "")).exists()}
    missing = [n for n in window if n not in have]
    if not missing:
        print(f"No new gauge file (latest: {names[0]}); nothing to do.")
        return False
    print(f"{len(missing)} new gauge snapshot(s) to make: {missing}")
    return True


def main():
    new = has_new()
    print(f"new={str(new).lower()}")
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as f:
            f.write(f"new={str(new).lower()}\n")


if __name__ == "__main__":
    main()
