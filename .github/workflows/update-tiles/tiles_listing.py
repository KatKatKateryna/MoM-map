"""Snapshot window settings and Final_Attributes CSV name helpers.

Standard library only, so the CI check for new CSVs (check_tiles_update.py) can use them
before the conda environment is set up; update_tiles.py imports the same definitions.
"""
import os
import re


def _env_int(name, default):
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


SNAPSHOTS_PER_DAY = 4
RETENTION_DAYS = _env_int(
    "RETENTION_DAYS", 7
)  # override via env for manual workflow_dispatch runs
MAX_SNAPSHOTS = SNAPSHOTS_PER_DAY * RETENTION_DAYS  # last 4×7 snapshots kept
ONLY_TIMESTAMP_PER_DAY = _env_bool(
    "ONLY_TIMESTAMP_PER_DAY", True
)  # True: keep only each day's latest snapshot, dropping the other 3/day
EFFECTIVE_MAX_SNAPSHOTS = RETENTION_DAYS if ONLY_TIMESTAMP_PER_DAY else MAX_SNAPSHOTS
OVERWRITE_EXISTING = _env_bool(
    "OVERWRITE_EXISTING", False
)  # clear tiles/metadata before this run


# CSV links in the MoM output server's directory listing
CSV_HREF_RE = re.compile(r'href="(Final_Attributes_[^"]+\.csv)"')

_TIMESTAMP_RE = re.compile(r"Final_Attributes_(\d{4})(\d{2})(\d{2})(\d{2})")


def _parse_timestamp(csv_name):
    """(YYYY, MM, DD, HH) tuple parsed from a Final_Attributes filename, or None."""
    m = _TIMESTAMP_RE.search(csv_name or "")
    return m.groups() if m else None


def timestamp_sort_key(csv_name):
    """Embedded YYYYMMDDHH as an int; unparseable names sort as oldest (-1)."""
    parts = _parse_timestamp(csv_name)
    return int("".join(parts)) if parts else -1


def keep_latest_per_day(items, csv_of, label=""):
    """From items already sorted newest-first, keep only the first (latest)
    one seen for each calendar day. Used when ONLY_TIMESTAMP_PER_DAY is set."""
    seen_days, kept = set(), []
    for item in items:
        csv_name = csv_of(item)
        parts = _parse_timestamp(csv_name)
        day = parts[:3] if parts else None
        hour = parts[3] if parts else None

        if day in seen_days:
            if hour == "12":
                print(
                    f"  [per-day{label}] replacing day {'-'.join(day)} entry with 12h: {csv_name}"
                )
                kept[-1] = item
            else:
                print(
                    f"  [per-day{label}] skipping {csv_name} (already have a later entry for {'-'.join(day)})"
                )
            continue

        print(f"  [per-day{label}] keeping  {csv_name}")
        seen_days.add(day)
        kept.append(item)
    return kept


# ── GloFAS gauges (threspoints_YYYYMMDDHH.txt, one file per day) ──────────────
# Listed next to the Final_Alert folder, in GLOFAS; kept RETENTION_DAYS files, newest first
GAUGE_HREF_RE = re.compile(r'href="(threspoints_\d{10}\.txt)"')

_GAUGE_TIMESTAMP_RE = re.compile(r"threspoints_(\d{4})(\d{2})(\d{2})(\d{2})")


def gauges_url(csv_url):
    """GLOFAS folder URL from MOM_CSV_URL (its /Final_Alert replaced), or None."""
    if not csv_url or "/Final_Alert" not in csv_url:
        return None
    return csv_url.replace("/Final_Alert", "/GLOFAS", 1)


def parse_gauge_timestamp(name):
    """(YYYY, MM, DD, HH) tuple parsed from a threspoints filename, or None."""
    m = _GAUGE_TIMESTAMP_RE.search(name or "")
    return m.groups() if m else None


def gauge_sort_key(name):
    """Embedded YYYYMMDDHH as an int; unparseable names sort as oldest (-1)."""
    parts = parse_gauge_timestamp(name)
    return int("".join(parts)) if parts else -1
