"""Aqueduct step 2: Aqueduct flood depths (m) as value PMTiles.

Dry cells (0) and negative cells become nodata, so lower zooms average flooded cells
only. The rasters are already on the 30" grid. Output, per layer:
  data/temp/aqueduct/<name>_4326.tif   (30", m, dry = nodata; also read by flood_extent/)
  data/persistent/aqueduct/<name>.pmtiles

Usage: python scripts/aqueduct/2_build_pmtiles.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
import layer_pmtiles as lp  # sets PROJ_LIB for GDAL before importing it

WORK = lp.TEMP / "aqueduct"
# (source file from 1_download.py, layer name, title)
LAYERS = [
    ("inuncoast_historical_wtsub_hist_rp1000_0.tif", "aqueduct_coastal_rp1000",
     "Aqueduct coastal flood depth, RP1000 (m)"),
    ("inunriver_historical_000000000WATCH_1980_rp01000.tif", "aqueduct_riverine_rp1000",
     "Aqueduct riverine flood depth, RP1000 (m)"),
]


def main():
    for src, name, title in LAYERS:
        if not (WORK / src).exists():
            sys.exit(f"missing {WORK / src}: run scripts/aqueduct/1_download.py first")
        out = WORK / f"{name}_4326.tif"
        print(f"{name}: dry -> nodata", flush=True)
        tmp = out.with_name(out.name + ".part")
        lp.dry_to_nodata(WORK / src, tmp)
        tmp.replace(out)
        lp.build_layer(out, WORK, "aqueduct", name, title)


if __name__ == "__main__":
    main()
