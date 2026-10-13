"""AI4G step 2: value PMTiles of the AI4G flood observations (1_download_reduce_30s.py output).

Keeps the classes on the 30" grid: 2 flooded at any time 2014-2024, 1 exclusion mask
(rough terrain, arid or urban), nodata elsewhere (0, never flooded, and no tile). Tiled
like the other ~1 km layers (common/layer_pmtiles.py, up to zoom 7), but with the maximum
instead of the average when zooming out, so classes never blend: a pixel with any
flooding stays 2. Uses whichever reduced tiles exist in data/temp/ai4g/30s/, so it can
run while that is still downloading: re-run it afterwards for the full set.

SRC.tif, a 30" mosaic from 1_download_reduce_30s.py --mosaic, is used instead of the
tiles; data/persistent/ai4g/ai4g_30s.tif is the one committed to the repo, so the layer
can be rebuilt without downloading anything.

Output:
  data/temp/ai4g/ai4g_flood_1km_4326.tif   (30", 1 and 2, nodata elsewhere)
  data/persistent/ai4g/ai4g_flood_1km.pmtiles

Usage: python scripts/ai4g/2_build_pmtiles.py [SRC.tif]
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
import layer_pmtiles as lp  # sets PROJ_LIB for GDAL before importing it
from osgeo import gdal

WORK = lp.TEMP / "ai4g"
TILES = WORK / "30s"
NAME = "ai4g_flood_1km"
TITLE = "Sentinel-1 flood observations 2014-2024 (AI4G): 2 flooded, 1 exclusion mask"


def flood_raster(out, src=None):
    """Global 30" Float32: the 1 and 2 classes, nodata elsewhere."""
    tiles = [str(src)] if src else sorted(str(p) for p in TILES.glob("*.tif"))
    if not tiles:
        sys.exit(f"no tiles in {TILES}: run scripts/ai4g/1_download_reduce_30s.py first, or pass SRC.tif")
    vrt = gdal.BuildVRT("", tiles, outputBounds=lp.GRID_30S[0], srcNodata=255, VRTNodata=255)
    dst = gdal.GetDriverByName("GTiff").Create(
        str(out), vrt.RasterXSize, vrt.RasterYSize, 1, gdal.GDT_Float32, lp.CREATE)
    dst.SetGeoTransform(vrt.GetGeoTransform())
    dst.SetProjection(vrt.GetProjection())
    band = dst.GetRasterBand(1)
    band.SetNoDataValue(lp.NODATA)
    src = vrt.GetRasterBand(1)
    flooded = 0
    for y in range(0, vrt.RasterYSize, 512):
        rows = min(512, vrt.RasterYSize - y)
        a = src.ReadAsArray(0, y, vrt.RasterXSize, rows)
        flooded += int((a == 2).sum())
        band.WriteArray(np.where((a == 1) | (a == 2), a, lp.NODATA).astype(np.float32), 0, y)
    dst = None
    return len(tiles), flooded


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    out = WORK / f"{NAME}_4326.tif"
    tmp = out.with_name(out.name + ".part")
    n, flooded = flood_raster(tmp, sys.argv[1] if len(sys.argv) > 1 else None)
    tmp.replace(out)
    print(f"{n} source file(s), {flooded:,} flooded 30\" cells", flush=True)
    lp.build_layer(out, WORK, "ai4g", NAME, TITLE, resample="max")


if __name__ == "__main__":
    main()
