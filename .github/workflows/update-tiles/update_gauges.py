#!/usr/bin/env python3
"""
Checks for new GloFAS gauge files (threspoints_YYYYMMDDHH.csv, one per day) in the GLOFAS
folder next to MOM_CSV_URL's Final_Alert. Each new one becomes a point PMTiles snapshot in
data/tiles/gauges/, in a rolling window of the newest RETENTION_DAYS files (listed in
data/tiles/gauges/metadata.json, like data/tiles/metadata.json for the watersheds).
Ends without error when there is no new file.

Usage:
    python update_gauges.py          # run once and exit
"""

import json
import os
import sys
import time
from io import StringIO
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

# gdal.VectorTranslate needs GDAL's own bundled proj.db, not pyproj's copy
# (which setup.ps1's PROJ_LIB points to) — always override before importing PROJ-dependent modules. No-op on CI (conda sets its own).
_venv_site_packages = (
    Path(__file__).resolve().parents[3] / ".venv" / "Lib" / "site-packages"
)
for _proj_dir in (
    _venv_site_packages / "osgeo" / "data" / "proj",  # GDAL's own copy
    _venv_site_packages
    / "pyproj"
    / "proj_dir"
    / "share"
    / "proj",  # pyproj's copy, fallback
):
    if _proj_dir.exists():
        os.environ["PROJ_LIB"] = str(_proj_dir)
        os.environ["PROJ_DATA"] = str(_proj_dir)
        break

import pandas as pd
import geopandas as gpd
import requests
from osgeo import gdal, ogr

# ── Paths ─────────────────────────────────────────────────────────────────────

load_dotenv()

# Repo root (this file is in .github/workflows/update-tiles/)
REPO_DIR = Path(__file__).resolve().parents[3]
# A folder of its own: update_tiles.py deletes every other *.pmtiles in data/tiles
OUT_DIR = REPO_DIR / "data" / "tiles" / "gauges"
GEOJSON_TMP = OUT_DIR / "gauges.geojson"
METADATA = OUT_DIR / "metadata.json"

OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Config ────────────────────────────────────────────────────────────────────

from tiles_listing import (  # noqa: E402  stdlib only, shared with check_gauges_update.py
    GAUGE_HREF_RE,
    OVERWRITE_EXISTING,
    RETENTION_DAYS,
    gauge_sort_key,
    gauges_url,
    parse_gauge_timestamp,
)

_gauges_url = gauges_url(os.getenv("MOM_CSV_URL"))
if not _gauges_url:
    print("ERROR: MOM_CSV_URL is not set or has no /Final_Alert to replace with /GLOFAS.")
    sys.exit(1)
GAUGES_BASE_URL: str = _gauges_url
MINZOOM = 2
MAXZOOM = 6

# Gauge files come one per day: ONLY_TIMESTAMP_PER_DAY does not apply
print(f"[config] RETENTION_DAYS={RETENTION_DAYS}  OVERWRITE_EXISTING={OVERWRITE_EXISTING}")

# ── File discovery ────────────────────────────────────────────────────────────


def fetch_listing():
    """All gauge files currently published at GAUGES_BASE_URL, newest first."""
    max_retries = 5
    retry_delay = 2  # seconds
    r = None

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(GAUGES_BASE_URL, timeout=30)
            r.raise_for_status()
            break
        except Exception as e:
            if attempt < max_retries:
                wait_time = retry_delay * attempt
                print(f"  Listing attempt {attempt} failed: {e}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"  Listing failed after {max_retries} attempts: {e}")
                raise

    ordered = sorted(set(GAUGE_HREF_RE.findall(r.text)), key=gauge_sort_key, reverse=True)
    print(f"  [listing] {len(ordered)} gauge file(s) found on server:")
    for n in ordered:
        print(f"    {n}")
    return [{"name": n, "download_url": GAUGES_BASE_URL + n} for n in ordered]


def parse_date_from_filename(name):
    parts = parse_gauge_timestamp(name)
    if not parts:
        return name
    y, mo, d, h = parts
    return f"{y}-{mo}-{d} {h}:00 UTC"


def snapshot_filename(name):
    parts = parse_gauge_timestamp(name)
    return f"gauges_{''.join(parts)}.pmtiles"


# ── Snapshot metadata (rolling window of RETENTION_DAYS) ──────────────────────


def load_snapshots():
    if not METADATA.exists():
        return []
    try:
        return json.loads(METADATA.read_text()).get("snapshots", [])
    except Exception:
        return []


def clear_existing_snapshots():
    """Wipe all gauge tiles + metadata.json (OVERWRITE_EXISTING=true)."""
    for tile_file in OUT_DIR.glob("*.pmtiles"):
        tile_file.unlink()
    METADATA.unlink(missing_ok=True)


def reconcile_snapshots(snapshots):
    """Re-sort by timestamp, dedup by file name, drop entries with missing tiles, keep the
    newest RETENTION_DAYS, reindex, write metadata.json and delete orphaned *.pmtiles."""
    ordered = sorted(snapshots, key=lambda s: gauge_sort_key(s.get("csv")), reverse=True)
    seen, combined = set(), []
    for snap in ordered:
        name = snap.get("csv")
        if name in seen or not (OUT_DIR / snap.get("file", "")).exists():
            print(f"  [reconcile] dropping {snap.get('file')} (duplicate or missing file)")
            continue
        seen.add(name)
        combined.append(snap)

    kept = combined[:RETENTION_DAYS]
    if len(combined) > RETENTION_DAYS:
        print(f"  [reconcile] trimmed to RETENTION_DAYS={RETENTION_DAYS}: dropped "
              f"{[s.get('csv') for s in combined[RETENTION_DAYS:]]}")
    for i, snap in enumerate(kept):
        snap["index"] = i

    keep_files = {snap.get("file") for snap in kept}
    for tile_file in OUT_DIR.glob("*.pmtiles"):
        if tile_file.name not in keep_files:
            print(f"  [reconcile] deleting orphaned tile {tile_file.name}")
            tile_file.unlink()

    METADATA.write_text(json.dumps({"snapshots": kept}, indent=2))
    print(f"  [reconcile] wrote metadata with {len(kept)} snapshot(s)")
    return kept


# ── File processing ───────────────────────────────────────────────────────────


def load_points(url):
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    try:
        text = r.content.decode("utf-8")
    except UnicodeDecodeError:
        text = r.content.decode("windows-1252")
    return pd.read_csv(StringIO(text))


def process_points(df):
    """Points from Lat / Lon with every column as a property, values as in the file."""
    return gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["Lon"], df["Lat"]), crs="EPSG:4326")


# ── PMTiles generation ────────────────────────────────────────────────────────


def build_pmtiles(gdf, name):
    if ogr.GetDriverByName("PMTiles") is None:
        print("ERROR: GDAL PMTiles driver not available. Ensure GDAL >= 3.7 is installed.")
        sys.exit(1)

    print(f"  Writing GeoJSON ({len(gdf)} points)...")
    gdf.to_file(str(GEOJSON_TMP), driver="GeoJSON")

    fname = snapshot_filename(name)
    tile_out = OUT_DIR / fname
    tmp_out = tile_out.with_suffix(".tmp.pmtiles")
    print(f"  Generating PMTiles (zoom {MINZOOM}-{MAXZOOM})...")
    options = gdal.VectorTranslateOptions(
        format="PMTiles",
        layerName="gauges",
        datasetCreationOptions=[f"MINZOOM={MINZOOM}", f"MAXZOOM={MAXZOOM}"],
    )
    result = gdal.VectorTranslate(str(tmp_out), str(GEOJSON_TMP), options=options)
    if result is None:
        raise RuntimeError("gdal.VectorTranslate failed — check GDAL error output above")
    result = None  # flush/close

    Path(tmp_out).replace(tile_out)
    GEOJSON_TMP.unlink(missing_ok=True)

    entry = {
        "updated_at": parse_date_from_filename(name),
        "csv": name,
        "file": fname,
        "generated": datetime.now(timezone.utc).isoformat(),
    }
    reconcile_snapshots([entry] + load_snapshots())
    print(f"  Done -> {tile_out}")


# ── Main ──────────────────────────────────────────────────────────────────────


def run_once():
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}] Checking for new gauge files...")

    if OVERWRITE_EXISTING:
        print(f"  OVERWRITE_EXISTING set — clearing {OUT_DIR} before rebuilding.")
        clear_existing_snapshots()

    reconcile_snapshots(load_snapshots())

    listing = fetch_listing()
    if not listing:
        print("  No gauge file found; nothing to do.")
        return

    window = listing[:RETENTION_DAYS]
    have = {
        snap["csv"]
        for snap in load_snapshots()
        if snap.get("csv") and (OUT_DIR / snap.get("file", "")).exists()
    }
    candidates = [c for c in window if c["name"] not in have]
    if not candidates:
        print(f'  No update (latest: {listing[0]["name"]}); nothing to do.')
        return

    print(f"  [run] {len(candidates)} file(s) to process: {[c['name'] for c in candidates]}")
    # Newest → oldest, so an interrupted backfill has already secured the latest data
    for info in candidates:
        print(f'  New gauge file: {info["name"]}')
        build_pmtiles(process_points(load_points(info["download_url"])), info["name"])


if __name__ == "__main__":
    run_once()
