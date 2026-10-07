"""Builds value PMTiles of the AI4G flood observations (build_ai4g_30s.py output).

Keeps the classes on the 30" grid: 2 flooded at any time 2014-2024, 1 exclusion mask
(rough terrain, arid or urban), nodata elsewhere (0, never flooded, and no tile). Tiled
like build_flood_pmtiles.py (up to zoom 7), but with the maximum instead of the average
when zooming out, so classes never blend: a pixel with any flooding stays 2. Uses whichever reduced
tiles exist in data/temp/ai4g_30s/, so it can run while that is still downloading:
re-run it afterwards for the full set. Output: data/persistent/ai4g/ai4g_flood_1km.pmtiles.

Usage: python scripts/build_ai4g_pmtiles.py
"""
import shutil

import numpy as np

import build_flood_pmtiles as bfp  # sets PROJ_LIB for GDAL before importing it
from osgeo import gdal

TILES = bfp.TEMP / "ai4g_30s"
NAME = "ai4g_flood_1km"
TITLE = "Sentinel-1 flood observations 2014-2024 (AI4G): 2 flooded, 1 exclusion mask"


def flood_raster(out):
    """Global 30" Float32: the 1 and 2 classes, nodata elsewhere."""
    tiles = sorted(str(p) for p in TILES.glob("*.tif"))
    vrt = gdal.BuildVRT("", tiles, outputBounds=(-180, -90, 180, 90), srcNodata=255, VRTNodata=255)
    dst = gdal.GetDriverByName("GTiff").Create(
        str(out), vrt.RasterXSize, vrt.RasterYSize, 1, gdal.GDT_Float32, bfp.CREATE)
    dst.SetGeoTransform(vrt.GetGeoTransform())
    dst.SetProjection(vrt.GetProjection())
    band = dst.GetRasterBand(1)
    band.SetNoDataValue(bfp.NODATA)
    src = vrt.GetRasterBand(1)
    flooded = 0
    for y in range(0, vrt.RasterYSize, 512):
        rows = min(512, vrt.RasterYSize - y)
        a = src.ReadAsArray(0, y, vrt.RasterXSize, rows)
        flooded += int((a == 2).sum())
        band.WriteArray(np.where((a == 1) | (a == 2), a, bfp.NODATA).astype(np.float32), 0, y)
    dst = None
    return len(tiles), flooded


def main():
    bfp.WORK.mkdir(parents=True, exist_ok=True)
    src = bfp.WORK / f"{NAME}_4326.tif"
    n, flooded = flood_raster(src)
    print(f"{n} tiles, {flooded:,} flooded 30\" cells", flush=True)
    bfp.build_layer(src, "ai4g", NAME, TITLE, resample="max")
    shutil.rmtree(bfp.WORK)


if __name__ == "__main__":
    main()
