"""Aqueduct step 1: downloads the two WRI Aqueduct Floods (v2) rasters the map uses.

Aqueduct Floods: https://aqueduct.wridata.org/AqueductFloods20/index.html (WRI, Ward et
al. 2020). Global 30" (~1 km) GeoTIFFs of inundation depth in metres, here the 1-in-1000
year return period under historical conditions (riverine maps as corrected by WRI on
2020-10-20):
  riverine  inunriver_historical_000000000WATCH_1980_rp01000.tif  (WATCH 1980 climate)
  coastal   inuncoast_historical_wtsub_hist_rp1000_0.tif  (historical sea level, wtsub =
            with subsidence)
Files go to data/temp/aqueduct/; ones already present with the server's size are skipped.

Usage: python scripts/aqueduct/1_download.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
from download import download

# The download page links the files relative to itself
BASE_URL = "https://aqueduct.wridata.org/AqueductFloods20/"
FILES = [
    "inunriver_historical_000000000WATCH_1980_rp01000.tif",
    "inuncoast_historical_wtsub_hist_rp1000_0.tif",
]
OUT = Path(__file__).resolve().parents[2] / "data" / "temp" / "aqueduct"


def main():
    failed = []
    for name in FILES:
        status, size = download(BASE_URL + name, OUT / name)
        print(f"{name} {status} ({size / 1e6:.1f} MB)", flush=True)
        if status.startswith("FAILED"):
            failed.append(name)
    if failed:
        sys.exit(f"{len(failed)} failed (re-run to retry): {', '.join(failed)}")


if __name__ == "__main__":
    main()
