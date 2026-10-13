"""GloFAS step 3: averages the cleaned GloFAS depths onto the 30" grid and builds PMTiles.

Input data/temp/glofas/RP500_<MODE>.vrt from 2_clean_mosaic.py (3", Int16 cm, -255
nodata; permanent water removed). The average only counts flood cells, so a 1 km cell
holds the mean depth of its flooded 90 m cells, in metres. Output:
  data/temp/glofas/glofas_rp500_1km_4326.tif   (30", m; also read by flood_extent/)
  data/persistent/glofas/glofas_rp500_1km.pmtiles

Usage: python scripts/glofas/3_build_pmtiles.py [MODE]   (default keep; also mask or deep,
       not flag: its -1 cells would be averaged as depths)
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
import layer_pmtiles as lp  # sets PROJ_LIB for GDAL before importing it
from osgeo import gdal

WORK = lp.TEMP / "glofas"
NAME = "glofas_rp500_1km"
TITLE = "GloFAS river flood depth, RP500, 1 km mean (m)"
SRC_NODATA = -255
OUT_30S = WORK / f"{NAME}_4326.tif"


def glofas_to_30s(vrt, out):
    """Mean depth of the flood cells in each 30" cell, metres."""
    (bounds, width, height) = lp.GRID_30S
    cm = out.with_name(out.stem + "_cm.part.tif")
    gdal.Warp(str(cm), str(vrt), format="GTiff", outputBounds=bounds, width=width, height=height,
              outputType=gdal.GDT_Float32, srcNodata=SRC_NODATA, dstNodata=lp.NODATA,
              resampleAlg="average", multithread=True, warpOptions=["NUM_THREADS=ALL_CPUS"],
              warpMemoryLimit=2048, creationOptions=lp.CREATE, callback=gdal.TermProgress_nocb)
    tmp = out.with_name(out.name + ".part")
    lp.map_strips(cm, tmp, lambda a: np.where(a == lp.NODATA, lp.NODATA, a / 100))
    cm.unlink()
    tmp.replace(out)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "keep"
    if mode not in ("keep", "mask", "deep"):
        sys.exit(__doc__)
    vrt = WORK / f"RP500_{mode}.vrt"
    if not vrt.exists():
        sys.exit(f"missing {vrt}: run scripts/glofas/2_clean_mosaic.py RP500 {mode} first")
    print(f"{NAME}: averaging {vrt.name} to 30\"", flush=True)
    glofas_to_30s(vrt, OUT_30S)
    lp.build_layer(OUT_30S, WORK, "glofas", NAME, TITLE)


if __name__ == "__main__":
    main()
