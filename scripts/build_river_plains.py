"""Builds raster PMTiles of the GFPLAN floodplain GeoTIFFs in data/temp/river_plains/.

Floodplain cells (value 0) become a muted blue, no-data (255) stays transparent.
Each zoom level is warped to Web Mercator once (area-averaged, so thin rivers fade
rather than vanish at low zooms) and cut into palette PNG tiles in parallel.

Usage: python scripts/build_river_plains.py OUT.pmtiles MAXZOOM AF [AS EU ...]
Several continents are mosaicked into one file. build() can also take a ready coverage
raster (`src`, 0-255) instead, and resample with "max" instead of "average", as
build_river_plains_30s.py does.
"""
import glob
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

SRC_DIR = Path(__file__).parent.parent.resolve() / "data" / "temp" / "river_plains"
BLUE = (74, 127, 181)  # #4a7fb5
NODATA = 255
TILE = 256
LEVELS = 16  # transparency steps per tile pixel (4-bit PNG)
HALF_WORLD = 20037508.342789244


def tile_size_m(z):
    return 2 * HALF_WORLD / 2 ** z


def coverage_vrt(continents, stem):
    """Single-band VRT: 255 where floodplain, 0 elsewhere (no-data included)."""
    sources = [glob.glob(str(SRC_DIR / c / "*.TIF"))[0] for c in continents]
    # Mosaic: no-data in one continent never hides data of an overlapping one
    gdal.BuildVRT(stem + "_mosaic.vrt", sources, srcNodata=NODATA, VRTNodata=NODATA)
    ds = gdal.Open(stem + "_mosaic.vrt", gdal.GA_Update)
    band = ds.GetRasterBand(1)
    ct = gdal.ColorTable()
    for value in range(256):
        ct.SetColorEntry(value, (0, 0, 0, 255 if value == 0 else 0))
    band.SetRasterColorTable(ct)
    band.SetRasterColorInterpretation(gdal.GCI_PaletteIndex)
    ds = None
    gdal.Translate(stem + "_rgba.vrt", stem + "_mosaic.vrt", format="VRT", rgbExpand="rgba")
    gdal.Translate(stem + "_alpha.vrt", stem + "_rgba.vrt", format="VRT", bandList=[4],
                   colorInterpretation=["gray"])  # plain values: averaged, not used as a mask
    return stem + "_alpha.vrt"


def warp_level(src, z, bounds, out, resample="average"):
    """Coverage at zoom z resolution, its extent snapped to that zoom's tile grid."""
    size = tile_size_m(z)
    minx, miny, maxx, maxy = bounds
    tx0 = math.floor((minx + HALF_WORLD) / size)
    tx1 = math.ceil((maxx + HALF_WORLD) / size)
    ty0 = math.floor((HALF_WORLD - maxy) / size)
    ty1 = math.ceil((HALF_WORLD - miny) / size)
    snapped = (tx0 * size - HALF_WORLD, HALF_WORLD - ty1 * size,
               tx1 * size - HALF_WORLD, HALF_WORLD - ty0 * size)
    gdal.Warp(out, src, dstSRS="EPSG:3857", outputBounds=snapped,
              width=(tx1 - tx0) * TILE, height=(ty1 - ty0) * TILE,
              resampleAlg=resample, multithread=True, warpOptions=["NUM_THREADS=ALL_CPUS"],
              creationOptions=["TILED=YES", "BLOCKXSIZE=256", "BLOCKYSIZE=256",
                               "COMPRESS=DEFLATE", "BIGTIFF=YES", "SPARSE_OK=TRUE"])
    return tx0, ty0, tx1 - tx0, ty1 - ty0


_png_ct = None


def encode_png(alpha):
    """4-bit palette PNG: one blue at LEVELS transparency steps."""
    global _png_ct
    if _png_ct is None:
        _png_ct = gdal.ColorTable()
        for value in range(LEVELS):
            _png_ct.SetColorEntry(value, (*BLUE, round(value * 255 / (LEVELS - 1))))
    alpha = np.rint(alpha.astype(np.float32) * (LEVELS - 1) / 255).astype(np.uint8)
    mem = gdal.GetDriverByName("MEM").Create("", TILE, TILE, 1, gdal.GDT_Byte)
    mem.GetRasterBand(1).SetRasterColorTable(_png_ct)
    mem.GetRasterBand(1).WriteArray(alpha)
    name = f"/vsimem/tile_{os.getpid()}.png"
    gdal.GetDriverByName("PNG").CreateCopy(name, mem, options=["ZLEVEL=9", "NBITS=4"])
    f = gdal.VSIFOpenL(name, "rb")
    gdal.VSIFSeekL(f, 0, 2)
    n = gdal.VSIFTellL(f)
    gdal.VSIFSeekL(f, 0, 0)
    data = gdal.VSIFReadL(1, n, f)
    gdal.VSIFCloseL(f)
    gdal.Unlink(name)
    return bytes(data)


def tile_row(args):
    """All non-empty tiles of one tile row of a zoom level's raster."""
    path, z, tx0, ty0, cols, row = args
    ds = gdal.Open(path)
    strip = ds.GetRasterBand(1).ReadAsArray(0, row * TILE, cols * TILE, TILE)
    out = []
    for col in range(cols):
        alpha = strip[:, col * TILE:(col + 1) * TILE]
        if alpha.max() >= 255 / (LEVELS - 1) / 2:  # anything left after quantising
            out.append((zxy_to_tileid(z, tx0 + col, ty0 + row), encode_png(alpha)))
    return out


def build(out_path, maxzoom, continents, work_dir, src=None, resample="average"):
    """src: a coverage raster (255 = all floodplain) to tile instead of the continents;
    resample "max" keeps a binary raster binary at lower zooms (any floodplain = opaque)"""
    os.makedirs(work_dir, exist_ok=True)
    stem = os.path.join(work_dir, Path(out_path).stem)
    if src is None:
        src = coverage_vrt(continents, stem)

    # Data extent in Web Mercator
    info = gdal.Info(src, format="json")["wgs84Extent"]["coordinates"][0]
    lons, lats = [p[0] for p in info], [p[1] for p in info]
    to_m = lambda lon, lat: (math.radians(lon) * 6378137.0,
                             math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * 6378137.0)
    minx, miny = to_m(min(lons), max(min(lats), -85.05))
    maxx, maxy = to_m(min(max(lons), 180), min(max(lats), 85.05))

    tiles = []
    with Pool() as pool:
        level_src = src
        for z in range(maxzoom, -1, -1):
            t = time.time()
            level = f"{stem}_z{z}.tif"
            tx0, ty0, cols, rows = warp_level(level_src, z, (minx, miny, maxx, maxy), level, resample)
            # Lower zooms average the level above: much less to read than the source
            level_src = level
            jobs = [(level, z, tx0, ty0, cols, row) for row in range(rows)]
            count = 0
            for result in pool.imap_unordered(tile_row, jobs):
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
                "tile_type": TileType.PNG,
                "tile_compression": Compression.NONE,
                "min_zoom": 0,
                "max_zoom": maxzoom,
                "min_lon_e7": int(min(lons) * 1e7),
                "min_lat_e7": int(min(lats) * 1e7),
                "max_lon_e7": int(min(max(lons), 180) * 1e7),
                "max_lat_e7": int(max(lats) * 1e7),
                "center_zoom": 3,
                "center_lon_e7": int((min(lons) + min(max(lons), 180)) / 2 * 1e7),
                "center_lat_e7": int((min(lats) + max(lats)) / 2 * 1e7),
            },
            {"name": "River floodplains", "attribution": "GFPLAN250m (Nardi et al.)"},
        )
    print(f"  {out_path}: {os.path.getsize(out_path) / 1e6:.1f} MB, {len(tiles)} tiles")


if __name__ == "__main__":
    out, zoom, *conts = sys.argv[1:]
    build(out, int(zoom), conts, os.path.join(os.path.dirname(os.path.abspath(out)), "_work"))
