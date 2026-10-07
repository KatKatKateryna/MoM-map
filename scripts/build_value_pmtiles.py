"""Builds value PMTiles from a single-band Web Mercator GeoTIFF (e.g. population).

Tiles hold values, not colors, so the map colors and filters them at runtime with a
MapLibre `color-relief` layer (js/extra-sources.js `values` entries). Each pixel stores
the index of the value on a log scale of 2% steps:
    index = round(log(value) / log(STEP_RATIO)) + STEP_OFFSET      (0 = no data / zero)
as a lossless RGB WebP (~1/3 smaller than PNG here) in the Terrarium encoding (index = R*256 + G + B/256 - 32768).
Whole indexes leave B at 0 and change R rarely, so the tiles compress far better than
raw values would. The archive metadata (`value_scale`) says how to turn values into
indexes. Lower zooms average the valid cells under each pixel (or take their maximum,
with resample="max", for classes).

Usage: python scripts/build_value_pmtiles.py SRC.tif OUT.pmtiles MAXZOOM [NAME]
"""
import math
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

# GDAL needs its own bundled proj.db, not pyproj's copy (same as update_tiles.py)
_venv_site_packages = (
    Path(__file__).parent.parent.resolve() / ".venv" / "Lib" / "site-packages"
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

import numpy as np
from osgeo import gdal
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

gdal.UseExceptions()
gdal.SetConfigOption("GDAL_CACHEMAX", "1024")

TILE = 256
HALF_WORLD = 20037508.342789244
LEVEL_NODATA = -9999.0  # no-data of the intermediate per-zoom rasters (exact in Float32)
# Log-scale step between stored values (finer than a color ramp or slider shows) and the
# index of value 1; values below STEP_RATIO ** (1 - STEP_OFFSET) (~5e-18) clip to index 1
STEP_RATIO = 1.02
STEP_OFFSET = 2000


def tile_size_m(z):
    return 2 * HALF_WORLD / 2 ** z


def warp_level(src, src_nodata, z, bounds, out, resample="average"):
    """Values at zoom z resolution, averaged, extent snapped to that zoom's tile grid."""
    size = tile_size_m(z)
    minx, miny, maxx, maxy = bounds
    tx0 = math.floor((minx + HALF_WORLD) / size)
    tx1 = math.ceil((maxx + HALF_WORLD) / size)
    ty0 = math.floor((HALF_WORLD - maxy) / size)
    ty1 = math.ceil((HALF_WORLD - miny) / size)
    snapped = (tx0 * size - HALF_WORLD, HALF_WORLD - ty1 * size,
               tx1 * size - HALF_WORLD, HALF_WORLD - ty0 * size)
    gdal.Warp(out, src, dstSRS="EPSG:3857", outputBounds=snapped,
              width=(tx1 - tx0) * TILE, height=(ty1 - ty0) * TILE, outputType=gdal.GDT_Float32,
              srcNodata=src_nodata, dstNodata=LEVEL_NODATA,
              resampleAlg=resample, multithread=True, warpOptions=["NUM_THREADS=ALL_CPUS"],
              creationOptions=["TILED=YES", "BLOCKXSIZE=256", "BLOCKYSIZE=256",
                               "COMPRESS=DEFLATE", "PREDICTOR=3", "BIGTIFF=YES", "SPARSE_OK=TRUE"])
    return tx0, ty0, tx1 - tx0, ty1 - ty0


def encode_tile(values):
    """Lossless Terrarium WebP of the log-step indexes of a 256x256 float array (0 where <= 0)."""
    index = np.zeros(values.shape, dtype=np.int64)
    pos = values > 0
    index[pos] = np.clip(np.rint(np.log(values[pos].astype(np.float64)) / math.log(STEP_RATIO)) + STEP_OFFSET,
                         1, 32767)
    n = index + 32768
    mem = gdal.GetDriverByName("MEM").Create("", TILE, TILE, 3, gdal.GDT_Byte)
    mem.GetRasterBand(1).WriteArray((n >> 8).astype(np.uint8))
    mem.GetRasterBand(2).WriteArray((n & 255).astype(np.uint8))
    mem.GetRasterBand(3).WriteArray(np.zeros(values.shape, dtype=np.uint8))
    name = f"/vsimem/tile_{os.getpid()}.webp"
    gdal.GetDriverByName("WEBP").CreateCopy(name, mem, options=["LOSSLESS=YES", "QUALITY=100"])
    f = gdal.VSIFOpenL(name, "rb")
    gdal.VSIFSeekL(f, 0, 2)
    size = gdal.VSIFTellL(f)
    gdal.VSIFSeekL(f, 0, 0)
    data = gdal.VSIFReadL(1, size, f)
    gdal.VSIFCloseL(f)
    gdal.Unlink(name)
    return bytes(data)


def tile_row(args):
    """All tiles with data in one tile row of a zoom level's raster."""
    path, z, tx0, ty0, cols, row = args
    ds = gdal.Open(path)
    strip = ds.GetRasterBand(1).ReadAsArray(0, row * TILE, cols * TILE, TILE)
    out = []
    for col in range(cols):
        values = strip[:, col * TILE:(col + 1) * TILE]
        valid = (values != LEVEL_NODATA) & np.isfinite(values) & (values > 0)
        if valid.any():
            out.append((zxy_to_tileid(z, tx0 + col, ty0 + row), encode_tile(np.where(valid, values, 0))))
    return out


def build(src, out_path, maxzoom, name, work_dir, resample="average"):
    os.makedirs(work_dir, exist_ok=True)
    stem = os.path.join(work_dir, Path(out_path).stem)
    ds = gdal.Open(src)
    src_nodata = ds.GetRasterBand(1).GetNoDataValue()
    gt = ds.GetGeoTransform()
    # Data extent, clipped to the Web Mercator world
    minx = max(gt[0], -HALF_WORLD)
    maxx = min(gt[0] + gt[1] * ds.RasterXSize, HALF_WORLD)
    maxy = min(gt[3], HALF_WORLD)
    miny = max(gt[3] + gt[5] * ds.RasterYSize, -HALF_WORLD)
    ds = None

    tiles = []
    with Pool() as pool:
        level_src, level_nodata = src, src_nodata
        for z in range(maxzoom, -1, -1):
            t = time.time()
            level = f"{stem}_z{z}.tif"
            tx0, ty0, cols, rows = warp_level(level_src, level_nodata, z, (minx, miny, maxx, maxy), level, resample)
            # Lower zooms average the level above: much less to read than the source
            level_src, level_nodata = level, LEVEL_NODATA
            jobs = [(level, z, tx0, ty0, cols, row) for row in range(rows)]
            count = 0
            for result in pool.imap_unordered(tile_row, jobs):
                tiles.extend(result)
                count += len(result)
            print(f"  z{z}: {count} tiles, {time.time() - t:.0f}s", flush=True)

    to_deg = lambda x, y: (math.degrees(x / 6378137.0),
                           math.degrees(2 * math.atan(math.exp(y / 6378137.0)) - math.pi / 2))
    (lon0, lat0), (lon1, lat1) = to_deg(minx, miny), to_deg(maxx, maxy)
    tiles.sort()
    with open(out_path, "wb") as f:
        writer = Writer(f)
        for tileid, data in tiles:
            writer.write_tile(tileid, data)
        writer.finalize(
            {
                "tile_type": TileType.WEBP,
                "tile_compression": Compression.NONE,
                "min_zoom": 0,
                "max_zoom": maxzoom,
                "min_lon_e7": int(lon0 * 1e7),
                "min_lat_e7": int(lat0 * 1e7),
                "max_lon_e7": int(lon1 * 1e7),
                "max_lat_e7": int(lat1 * 1e7),
                "center_zoom": 2,
                "center_lon_e7": int((lon0 + lon1) / 2 * 1e7),
                "center_lat_e7": int((lat0 + lat1) / 2 * 1e7),
            },
            {"name": name, "encoding": "terrarium",
             "value_scale": {"type": "log", "ratio": STEP_RATIO, "offset": STEP_OFFSET}},
        )
    print(f"  {out_path}: {os.path.getsize(out_path) / 1e6:.1f} MB, {len(tiles)} tiles")


if __name__ == "__main__":
    src, out, zoom, *rest = sys.argv[1:]
    name = rest[0] if rest else Path(out).stem
    build(src, out, int(zoom), name, os.path.join(os.path.dirname(os.path.abspath(out)), "_work"))
