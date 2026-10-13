"""Shared steps of the ~1 km value layers (flood depths, flood extent, AI4G, population).

Each dataset's scripts bring their data onto the global 30" grid (GRID_30S: 1/120 degree,
-180..180, -90..90, the Aqueduct grid) in EPSG:4326 as Float32 with NODATA, or hand over
a raster in any projection (population). build_layer() then warps it to Web Mercator at
the zoom-8 pixel size and tiles it up to MAXZOOM 7 (build_value_pmtiles.py); finer
layers can go higher and be limited to a box.

Rasters go to data/temp/<dataset>/ and are kept; only the per-zoom tiling rasters are
removed. PMTiles go to data/persistent/<folder>/<name>.pmtiles.
"""
import shutil
import time
from pathlib import Path

import numpy as np

import build_value_pmtiles as bvp  # sets PROJ_LIB for GDAL before importing it
from osgeo import gdal

ROOT = Path(__file__).resolve().parents[2]
TEMP = ROOT / "data" / "temp"
PERSISTENT = ROOT / "data" / "persistent"
NODATA = -9999.0
MAXZOOM = 7
CREATE = ["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=3", "BIGTIFF=YES", "SPARSE_OK=TRUE"]
# (bounds, width, height) of the global 30" grid
GRID_30S = ((-180, -90, 180, 90), 43200, 21600)


def map_strips(src, out, fn):
    """Float32 copy of src with fn applied to each strip of 512 rows; nodata NODATA."""
    ds = gdal.Open(str(src))
    band = ds.GetRasterBand(1)
    dst = gdal.GetDriverByName("GTiff").Create(
        str(out), ds.RasterXSize, ds.RasterYSize, 1, gdal.GDT_Float32, CREATE)
    dst.SetGeoTransform(ds.GetGeoTransform())
    dst.SetProjection(ds.GetProjection())
    out_band = dst.GetRasterBand(1)
    out_band.SetNoDataValue(NODATA)
    for y in range(0, ds.RasterYSize, 512):
        rows = min(512, ds.RasterYSize - y)
        out_band.WriteArray(fn(band.ReadAsArray(0, y, ds.RasterXSize, rows)).astype(np.float32), 0, y)
    dst = None


def dry_to_nodata(src, out):
    """Copy with 0 (dry) and negative cells set to nodata."""
    map_strips(src, out, lambda a: np.where(a > 0, a, NODATA))


def to_mercator(src, out, resample="average", src_nodata=None, maxzoom=MAXZOOM, bbox=None, scale=1):
    """Web Mercator at the pixel size of zoom maxzoom + 1, averaging valid cells (or their
    maximum). src_nodata defaults to the source's own nodata value; bbox (west, south,
    east, north in degrees) limits it to that box, else the whole world; valid values
    are multiplied by scale."""
    res = bvp.tile_size_m(maxzoom + 1) / bvp.TILE
    bounds = dict(outputBounds=bbox, outputBoundsSRS="EPSG:4326") if bbox else \
        dict(outputBounds=(-bvp.HALF_WORLD, -bvp.HALF_WORLD, bvp.HALF_WORLD, bvp.HALF_WORLD))
    tmp = out.with_name(out.name + ".part")
    gdal.Warp(str(tmp), str(src), format="GTiff", dstSRS="EPSG:3857", xRes=res, yRes=res, **bounds,
              srcNodata=src_nodata, dstNodata=NODATA, outputType=gdal.GDT_Float32,
              resampleAlg=resample, targetAlignedPixels=True,
              multithread=True, warpOptions=["NUM_THREADS=ALL_CPUS"], warpMemoryLimit=2048,
              creationOptions=CREATE, callback=gdal.TermProgress_nocb)
    if scale != 1:
        scaled = out.with_name(out.name + ".scaled.part")
        map_strips(tmp, scaled, lambda a: np.where(a == NODATA, NODATA, a * scale))
        tmp.unlink()
        tmp = scaled
    tmp.replace(out)


def build_layer(src, work, folder, name, title, resample="average", src_nodata=None,
                maxzoom=MAXZOOM, bbox=None, scale=1):
    """src -> work/<name>_3857.tif (kept) -> data/persistent/<folder>/<name>.pmtiles,
    zoom 0 to maxzoom; bbox and scale as in to_mercator()."""
    t0 = time.time()
    work.mkdir(parents=True, exist_ok=True)
    merc = work / f"{name}_3857.tif"
    print(f"{name}: Web Mercator", flush=True)
    to_mercator(src, merc, resample, src_nodata, maxzoom, bbox, scale)
    out_dir = PERSISTENT / folder
    out_dir.mkdir(parents=True, exist_ok=True)
    levels = work / f"{name}_levels"
    print(f"{name}: tiles", flush=True)
    bvp.build(str(merc), str(out_dir / f"{name}.pmtiles"), maxzoom, title, str(levels), resample)
    shutil.rmtree(levels)
    print(f"{name}: done in {time.time() - t0:.0f}s", flush=True)
