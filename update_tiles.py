#!/usr/bin/env python3
"""
Checks for a new Final_Attributes CSV at the MoM output server. If found:
joins with watershed shapefile, adds a snapshot to the rolling window.

Usage:
    python update_tiles.py          # run once and exit
"""

import json
import os
import re
import sys
import time
from io import StringIO
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

# gdal.VectorTranslate needs GDAL's own bundled proj.db, not pyproj's copy
# (which setup.ps1's PROJ_LIB points to) — always override before importing PROJ-dependent modules. No-op on CI (conda sets its own).
_venv_site_packages = (
    Path(__file__).parent.resolve() / ".venv" / "Lib" / "site-packages"
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

REPO_DIR = Path(__file__).parent.resolve()
SHP_PATH = REPO_DIR / "data" / "watershed_shp" / "Watershed_pfaf_id.shp"
OUT_DIR = REPO_DIR / "data" / "tiles"
GEOJSON_TMP = OUT_DIR / "watersheds.geojson"
METADATA = OUT_DIR / "metadata.json"

if not os.path.exists(OUT_DIR):
    os.mkdir(OUT_DIR)

# ── Config ────────────────────────────────────────────────────────────────────

_csv_url = os.getenv("MOM_CSV_URL")
if not _csv_url:
    print("ERROR: MOM_CSV_URL environment variable is not set.")
    sys.exit(1)
CSV_BASE_URL: str = _csv_url
ALERT_RANK = {"Warning": 3, "Watch": 2, "Advisory": 1, "Information": 0}
MINZOOM = 2
MAXZOOM = 6


from tiles_listing import (  # noqa: E402  stdlib only, shared with check_tiles_update.py
    CSV_HREF_RE,
    EFFECTIVE_MAX_SNAPSHOTS,
    MAX_SNAPSHOTS,
    ONLY_TIMESTAMP_PER_DAY,
    OVERWRITE_EXISTING,
    RETENTION_DAYS,
    SNAPSHOTS_PER_DAY,
    _parse_timestamp,
    keep_latest_per_day,
    timestamp_sort_key,
)

print(
    f"[config] RETENTION_DAYS={RETENTION_DAYS}  ONLY_TIMESTAMP_PER_DAY={ONLY_TIMESTAMP_PER_DAY}"
    f"  EFFECTIVE_MAX_SNAPSHOTS={EFFECTIVE_MAX_SNAPSHOTS}  OVERWRITE_EXISTING={OVERWRITE_EXISTING}"
)

# ── CSV discovery ─────────────────────────────────────────────────────────────


def fetch_csv_listing():
    """All CSVs currently published at CSV_BASE_URL, newest name first."""
    max_retries = 5
    retry_delay = 2  # seconds
    r = None

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(CSV_BASE_URL, timeout=30)
            r.raise_for_status()
            break  # Success, exit the loop
        except Exception as e:
            if attempt < max_retries:
                wait_time = retry_delay * attempt
                print(
                    f"  CSV fetch attempt {attempt} failed: {e}. Retrying in {wait_time}s..."
                )
                time.sleep(wait_time)
            else:
                print(f"  CSV fetch failed after {max_retries} attempts: {e}")
                raise

    # Parse response
    raw_names = CSV_HREF_RE.findall(r.text)
    print(
        f"  [listing] {len(raw_names)} raw href(s) matched, {len(set(raw_names))} unique"
    )
    names = set(raw_names)
    ordered = sorted(names, key=timestamp_sort_key, reverse=True)
    print(f"  [listing] {len(ordered)} CSVs found on server:")
    for n in ordered:
        print(f"    {n}")
    return [{"name": n, "download_url": CSV_BASE_URL + n} for n in ordered]




def parse_date_from_filename(name):
    parts = _parse_timestamp(name)
    if not parts:
        return name
    y, mo, d, h = parts
    return f"{y}-{mo}-{d} {h}:00 UTC"


def snapshot_filename(csv_name):
    parts = _parse_timestamp(csv_name)
    stamp = "".join(parts) if parts else re.sub(r"\W+", "", csv_name)
    return f"watersheds_{stamp}.pmtiles"


# ── Snapshot metadata (rolling window of MAX_SNAPSHOTS) ───────────────────────


def load_snapshots():
    if not METADATA.exists():
        return []
    try:
        return json.loads(METADATA.read_text()).get("snapshots", [])
    except Exception:
        return []


def clear_existing_snapshots():
    """Wipe all tiles + metadata.json so the run starts from an empty window
    (OVERWRITE_EXISTING=true) — used for a manual, from-scratch rebuild."""
    for tile_file in OUT_DIR.glob("*.pmtiles"):
        tile_file.unlink()
    METADATA.unlink(missing_ok=True)


def reconcile_snapshots(snapshots):
    """Re-sort by timestamp, dedup by csv, drop entries with missing files,
    optionally collapse to one-per-day, keep newest EFFECTIVE_MAX_SNAPSHOTS, reindex, write metadata.json, and delete any orphaned *.pmtiles files.
    """
    print(f"  [reconcile] input: {len(snapshots)} snapshot(s)")
    ordered = sorted(
        snapshots, key=lambda s: timestamp_sort_key(s.get("csv")), reverse=True
    )

    seen_csv = set()
    combined = []
    for snap in ordered:
        csv = snap.get("csv")
        file_path = OUT_DIR / snap.get("file", "")
        if csv in seen_csv:
            print(f"  [reconcile] dropping duplicate csv: {csv}")
            continue
        if not file_path.exists():
            print(
                f"  [reconcile] dropping missing file: {snap.get('file')} (csv={csv})"
            )
            continue
        seen_csv.add(csv)
        combined.append(snap)

    print(f"  [reconcile] after dedup/missing-file check: {len(combined)} snapshot(s)")

    if ONLY_TIMESTAMP_PER_DAY:
        combined = keep_latest_per_day(
            combined, lambda s: s.get("csv"), label="/reconcile"
        )
        print(f"  [reconcile] after per-day filter: {len(combined)} snapshot(s)")

    kept = combined[:EFFECTIVE_MAX_SNAPSHOTS]
    if len(combined) > EFFECTIVE_MAX_SNAPSHOTS:
        dropped = combined[EFFECTIVE_MAX_SNAPSHOTS:]
        print(
            f"  [reconcile] trimmed to EFFECTIVE_MAX_SNAPSHOTS={EFFECTIVE_MAX_SNAPSHOTS};"
            f" dropped {len(dropped)}: {[s.get('csv') for s in dropped]}"
        )
    for i, snap in enumerate(kept):
        snap["index"] = i

    keep_files = {snap.get("file") for snap in kept}
    orphans = [f.name for f in OUT_DIR.glob("*.pmtiles") if f.name not in keep_files]
    if orphans:
        print(f"  [reconcile] deleting {len(orphans)} orphaned tile(s): {orphans}")
    for tile_file in OUT_DIR.glob("*.pmtiles"):
        if tile_file.name not in keep_files:
            tile_file.unlink()

    print(f"  [reconcile] wrote metadata with {len(kept)} snapshot(s)")
    METADATA.write_text(json.dumps({"snapshots": kept}, indent=2))
    return kept


def save_snapshot(entry):
    """Prepend a freshly generated snapshot and reconcile the rolling window."""
    reconcile_snapshots([entry] + load_snapshots())


# ── CSV processing ────────────────────────────────────────────────────────────


def load_csv(url):
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    try:
        text = r.content.decode("utf-8")
    except UnicodeDecodeError:
        text = r.content.decode("windows-1252")
    return pd.read_csv(StringIO(text))


def process_csv(df):
    """Dedup by pfaf_id: pick highest-alert row, merge country/region names."""
    groups = {}
    for _, row in df.iterrows():
        pfaf = str(row.get("pfaf_id", "") or "")
        if pfaf:
            groups.setdefault(pfaf, []).append(row)

    records = []
    for pfaf, rows in groups.items():
        base = max(rows, key=lambda r: ALERT_RANK.get(r.get("Alert", ""), -1))
        countries = ", ".join(
            sorted({str(r["name"]) for r in rows if pd.notna(r.get("name"))})
        )
        regions = ", ".join(
            sorted({str(r["name_1"]) for r in rows if pd.notna(r.get("name_1"))})
        )
        # Hazard_Score uses a large negative sentinel (~-3000) for "no data";
        # treat anything below -1000 as missing rather than a real score.
        hazard_score = base.get("Hazard_Score")
        records.append(
            {
                "pfaf_id": int(float(pfaf)),
                "alert": base.get("Alert") if pd.notna(base.get("Alert")) else None,
                "status": base.get("Status") if pd.notna(base.get("Status")) else None,
                "name": countries or None,
                "regions": regions or None,
                "days_until_peak": (
                    int(base["Days_until_peak"])
                    if pd.notna(base.get("Days_until_peak"))
                    else None
                ),
                "hazard_score": (
                    round(float(hazard_score), 1)
                    if pd.notna(hazard_score) and hazard_score >= -1000
                    else None
                ),
                "riverine_risk": (
                    round(float(base["Scaled_Riverine_Risk"]), 1)
                    if pd.notna(base.get("Scaled_Riverine_Risk"))
                    else None
                ),
                "coastal_risk": (
                    round(float(base["Scaled_Coastal_Risk"]), 1)
                    if pd.notna(base.get("Scaled_Coastal_Risk"))
                    else None
                ),
            }
        )
    return pd.DataFrame(records)


# ── PMTiles generation ────────────────────────────────────────────────────────


def regenerate_pmtiles(alert_df, csv_name):
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if ogr.GetDriverByName("PMTiles") is None:
        print(
            "ERROR: GDAL PMTiles driver not available. Ensure GDAL >= 3.7 is installed."
        )
        sys.exit(1)

    print("  Loading shapefile...")
    gdf = gpd.read_file(SHP_PATH)
    gdf["pfaf_id"] = pd.to_numeric(gdf["pfaf_id"], errors="coerce").astype("Int64")
    alert_df["pfaf_id"] = alert_df["pfaf_id"].astype("Int64")

    print("  Joining alert data...")
    merged = gdf.merge(alert_df, on="pfaf_id", how="left")

    # Fix any invalid geometries before tiling
    merged.geometry = merged.geometry.buffer(0)

    print("  Writing GeoJSON...")
    merged.to_file(str(GEOJSON_TMP), driver="GeoJSON")

    fname = snapshot_filename(csv_name)
    tile_out = OUT_DIR / fname
    tmp_out = tile_out.with_suffix(".tmp.pmtiles")
    print(f"  Generating PMTiles (zoom {MINZOOM}–{MAXZOOM})...")
    options = gdal.VectorTranslateOptions(
        format="PMTiles",
        layerName="watersheds",
        datasetCreationOptions=[
            f"MINZOOM={MINZOOM}",
            f"MAXZOOM={MAXZOOM}",
            "SIMPLIFICATION=10",
            "SIMPLIFICATION_MAX_ZOOM=2",  # preserve more detail at max zoom
        ],
    )
    result = gdal.VectorTranslate(str(tmp_out), str(GEOJSON_TMP), options=options)
    if result is None:
        raise RuntimeError(
            "gdal.VectorTranslate failed — check GDAL error output above"
        )
    result = None  # flush/close

    Path(tmp_out).replace(tile_out)
    GEOJSON_TMP.unlink(missing_ok=True)

    entry = {
        "updated_at": parse_date_from_filename(csv_name),
        "csv": csv_name,
        "file": fname,
        "generated": datetime.now(timezone.utc).isoformat(),
    }
    save_snapshot(entry)

    print(f"  Done → {tile_out}")


# ── Main loop ─────────────────────────────────────────────────────────────────


def run_once():
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}] Checking for new CSVs...")

    if OVERWRITE_EXISTING:
        print(f"  OVERWRITE_EXISTING set — clearing {OUT_DIR} before rebuilding.")
        clear_existing_snapshots()

    # Enforce the current MAX_SNAPSHOTS window up front, independent of network access.
    reconcile_snapshots(load_snapshots())

    try:
        listing = fetch_csv_listing()
    except Exception as e:
        print(f"  CSV fetching error: {e}")
        sys.exit(1)

    if not listing:
        print("  No CSV found.")
        sys.exit(1)

    print(f"  [run] raw listing: {len(listing)} CSV(s)")

    if ONLY_TIMESTAMP_PER_DAY:
        listing = keep_latest_per_day(listing, lambda c: c["name"], label="/listing")
        print(f"  [run] after per-day filter: {len(listing)} CSV(s)")

    window = listing[:EFFECTIVE_MAX_SNAPSHOTS]
    print(
        f"  [run] window (newest {EFFECTIVE_MAX_SNAPSHOTS}): {[c['name'] for c in window]}"
    )
    if len(listing) > EFFECTIVE_MAX_SNAPSHOTS:
        outside = listing[EFFECTIVE_MAX_SNAPSHOTS:]
        print(
            f"  [run] outside window (not considered): {[c['name'] for c in outside]}"
        )

    # Only counts as "have" if the tile file actually exists — a metadata
    # entry with a missing file (interrupted run, partial restore) gets regenerated below.
    snapshots = load_snapshots()
    have = {
        snap["csv"]
        for snap in snapshots
        if snap.get("csv") and (OUT_DIR / snap.get("file", "")).exists()
    }
    print(f"  [run] already have {len(have)} tile(s): {sorted(have)}")

    # Backfill: process every CSV in the newest window that isn't captured
    # yet, so a fresh data/tiles fills up in one run.
    candidates = [c for c in window if c["name"] not in have]
    skipped = [c["name"] for c in window if c["name"] in have]
    if skipped:
        print(f"  [run] skipping {len(skipped)} already-tiled CSV(s): {skipped}")

    if not candidates:
        print(f'  No update (latest: {listing[0]["name"]})')
        sys.exit(1)

    print(
        f"  [run] {len(candidates)} candidate(s) to process: {[c['name'] for c in candidates]}"
    )
    if len(candidates) > 1:
        print(f"  Backfilling {len(candidates)} missing snapshot(s)...")

    # Newest → oldest, so an interrupted backfill has already secured the latest data.
    for info in candidates:
        print(f'  New CSV: {info["name"]}')
        try:
            df = load_csv(info["download_url"])
            alert_df = process_csv(df)
            regenerate_pmtiles(alert_df, info["name"])
        except Exception as e:
            print(f"  Error processing {info['name']}: {e}")
            raise


if __name__ == "__main__":

    run_once()
