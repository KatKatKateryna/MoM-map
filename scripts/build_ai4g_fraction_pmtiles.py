"""Builds value PMTiles of an AI4G flooded-fraction GeoTIFF (build_ai4g_5s_se.py output).

Like build_ai4g_pmtiles.py, but for fractions instead of classes, and for any SRC.tif
given as the argument. SRC holds the flooded share of each cell in % (0..100), MASKED
(101) for the exclusion mask and its nodata value for no tile. The tiles store the same
values on a linear scale: lossless Terrarium WebP, value = R*256 + G + B/256 - 32768, so
values keep 1/256 precision (within 0.002 percentage points). Nodata is stored as NODATA
(-255), or as SRC's nodata value if that is lower (e.g. -999). The archive metadata
records the nodata value and the range of the other values, the map's default display
range: value_scale {"type": "linear", "nodata": ..., "min": ..., "max": ...}.
Tiled like build_value_pmtiles.py (whose log scale is left as it is for the other
layers), but lower zooms take the maximum, never the average: MASKED is above any share,
so a pixel is MASKED where any cell under it is masked, else it holds the highest flooded
share under it. Tiled up to MAXZOOM (default 10, ~150 m pixels, about the 5"
source). Intermediate rasters go to data/temp/<name>_work/, removed at the end. Output:
OUT.pmtiles or data/persistent/ai4g/<SRC name>.pmtiles.

Usage: python scripts/build_ai4g_fraction_pmtiles.py SRC.tif [OUT.pmtiles] [MAXZOOM]
"""
import math
import os
import shutil
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

import build_flood_pmtiles as bfp  # sets PROJ_LIB for GDAL before importing it
import build_value_pmtiles as bvp
from osgeo import gdal
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

NODATA = -255  # nodata in the tiles, unless SRC's nodata value is lower
MASKED = 101
MAXZOOM = 10


def encode_tile(values):
    """Lossless Terrarium WebP of a 256x256 float array, values as they are (1/256 steps)."""
    n = np.clip(np.rint((values.astype(np.float64) + 32768) * 256), 0, 2 ** 24 - 1).astype(np.int64)
    mem = gdal.GetDriverByName("MEM").Create("", bvp.TILE, bvp.TILE, 3, gdal.GDT_Byte)
    for k, channel in enumerate((n >> 16, (n >> 8) & 255, n & 255), 1):
        mem.GetRasterBand(k).WriteArray(channel.astype(np.uint8))
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


def source_values(src):
    """(SRC's nodata value, the nodata value for the tiles, min and max of the other values)."""
    ds = gdal.Open(src)
    band = ds.GetRasterBand(1)
    src_nodata = band.GetNoDataValue()
    nodata = NODATA if src_nodata is None else min(src_nodata, NODATA)
    lo, hi = math.inf, -math.inf
    for y in range(0, ds.RasterYSize, 512):
        a = band.ReadAsArray(0, y, ds.RasterXSize, min(512, ds.RasterYSize - y))
        a = a[np.isfinite(a) & (a != src_nodata if src_nodata is not None else True)]
        if a.size:
            lo, hi = min(lo, float(a.min())), max(hi, float(a.max()))
    if lo <= nodata or nodata < -32768 or hi >= 32768:
        sys.exit(f"{src}: values {lo}..{hi} with nodata {nodata} do not fit the encoding")
    return src_nodata, nodata, lo, hi


def tile_row(args):
    """All tiles with data in one tile row of a zoom level's raster, nodata as nodata."""
    path, z, tx0, ty0, cols, row, nodata = args
    ds = gdal.Open(path)
    strip = ds.GetRasterBand(1).ReadAsArray(0, row * bvp.TILE, cols * bvp.TILE, bvp.TILE)
    out = []
    for col in range(cols):
        values = strip[:, col * bvp.TILE:(col + 1) * bvp.TILE]
        valid = (values != bvp.LEVEL_NODATA) & np.isfinite(values)
        if valid.any():
            out.append((zxy_to_tileid(z, tx0 + col, ty0 + row),
                        encode_tile(np.where(valid, values, nodata))))
    return out


def build(src, out_path, maxzoom, name, work_dir):
    """build_value_pmtiles.build with the linear encoding and the maximum for lower zooms."""
    os.makedirs(work_dir, exist_ok=True)
    stem = os.path.join(work_dir, Path(out_path).stem)
    src_nodata, nodata, lo, hi = source_values(src)
    print(f"{src}: values {lo:g}..{hi:g}, nodata {src_nodata} -> {nodata:g}", flush=True)
    ds = gdal.Open(src)
    gt = ds.GetGeoTransform()
    w, n = gt[0], gt[3]
    e, s = gt[0] + gt[1] * ds.RasterXSize, gt[3] + gt[5] * ds.RasterYSize
    ds = None
    # Data extent in Web Mercator
    to_m = lambda lon, lat: (math.radians(lon) * 6378137.0,
                             math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * 6378137.0)
    (minx, miny), (maxx, maxy) = to_m(w, s), to_m(e, n)

    tiles = []
    with Pool() as pool:
        level_src, level_nodata = src, src_nodata
        for z in range(maxzoom, -1, -1):
            t = time.time()
            level = f"{stem}_z{z}.tif"
            tx0, ty0, cols, rows = bvp.warp_level(level_src, level_nodata, z, (minx, miny, maxx, maxy),
                                                  level, "max")
            # Lower zooms take the maximum of the level above: much less to read than the source
            level_src, level_nodata = level, bvp.LEVEL_NODATA
            count = 0
            for result in pool.imap_unordered(tile_row, [(level, z, tx0, ty0, cols, row, nodata)
                                                         for row in range(rows)]):
                tiles.extend(result)
                count += len(result)
            print(f"  z{z}: {count} tiles, {time.time() - t:.0f}s", flush=True)

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
                "min_lon_e7": int(w * 1e7),
                "min_lat_e7": int(s * 1e7),
                "max_lon_e7": int(e * 1e7),
                "max_lat_e7": int(n * 1e7),
                "center_zoom": 5,
                "center_lon_e7": int((w + e) / 2 * 1e7),
                "center_lat_e7": int((s + n) / 2 * 1e7),
            },
            {"name": name, "encoding": "terrarium",
             "value_scale": {"type": "linear", "nodata": nodata, "min": lo, "max": hi}},
        )
    print(f"  {out_path}: {os.path.getsize(out_path) / 1e6:.1f} MB, {len(tiles)} tiles")


def main():
    if not 2 <= len(sys.argv) <= 4:
        sys.exit(__doc__)
    src = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else bfp.PERSISTENT / "ai4g" / f"{src.stem}.pmtiles"
    maxzoom = int(sys.argv[3]) if len(sys.argv) > 3 else MAXZOOM
    title = (f"Sentinel-1 flood observations 2014-2024 (AI4G), {src.stem}: "
             f"flooded share 0-100 %, {MASKED} exclusion mask")
    work = bfp.TEMP / f"{out.stem}_work"
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    build(str(src), str(out), maxzoom, title, str(work))
    shutil.rmtree(work)
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
