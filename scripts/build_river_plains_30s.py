"""Builds a 30" (Aqueduct grid) version of the GFPLAN250m floodplains and its PMTiles.

The six continent GeoTIFFs in data/temp/river_plains/ (8.33", 0 = floodplain) are
mosaicked onto the global 30" grid, staying binary: a cell is floodplain (255) if any
source cell overlapping it is, else 0. 8.33" does not divide 30", so a source cell on
an edge counts for both cells it overlaps. The raster is kept as
data/temp/river_plains_30s.tif and tiled by build_river_plains.py (same blue) up to
MAXZOOM, with "max" at every zoom so it stays opaque when zoomed out.

Usage: python scripts/build_river_plains_30s.py [MAXZOOM]   (default 7, like the flood layers)
"""
import shutil
import sys
import time
from pathlib import Path

import build_river_plains as brp  # sets PROJ_LIB for GDAL before importing it
from osgeo import gdal

ROOT = Path(__file__).parent.parent
TEMP = ROOT / "data" / "temp"
OUT_TIF = TEMP / "river_plains_30s.tif"
OUT_PMTILES = ROOT / "data" / "persistent" / "river_plains" / "river_plains_30s.pmtiles"
WORK = TEMP / "river_plains_30s_work"
CONTINENTS = ["AF", "AS", "EU", "NA", "OC", "SA"]


def main():
    maxzoom = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    t0 = time.time()
    WORK.mkdir(parents=True, exist_ok=True)
    coverage = brp.coverage_vrt(CONTINENTS, str(WORK / "river_plains"))
    print(f"max to 30\" -> {OUT_TIF}", flush=True)
    tmp = OUT_TIF.with_name(OUT_TIF.name + ".part")
    gdal.Warp(str(tmp), coverage, format="GTiff", outputBounds=(-180, -90, 180, 90),
              width=43200, height=21600, outputType=gdal.GDT_Byte, resampleAlg="max",
              multithread=True, warpOptions=["NUM_THREADS=ALL_CPUS"], warpMemoryLimit=2048,
              creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=2", "BIGTIFF=YES",
                               "SPARSE_OK=TRUE"],
              callback=gdal.TermProgress_nocb)
    tmp.replace(OUT_TIF)
    ds = gdal.Open(str(OUT_TIF), gdal.GA_Update)
    ds.GetRasterBand(1).SetDescription("floodplain_255")
    ds = None

    print(f"tiles -> {OUT_PMTILES}", flush=True)
    brp.build(str(OUT_PMTILES), maxzoom, None, str(WORK / "levels"), src=str(OUT_TIF), resample="max")
    shutil.rmtree(WORK)
    print(f"Done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
