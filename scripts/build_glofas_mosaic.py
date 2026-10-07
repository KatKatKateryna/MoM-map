"""Cleans the GloFAS flood depth tiles (JRC CEMS) and merges them into a global mosaic.

Permanent water (rivers, lakes) is patched into the depth maps by JRC; it is always
removed here so only flooding is left. The spurious-depth layer flags areas where the
hydraulic model predicts >10 m depths in small channels (<3,000 km2) for RP10, plus a
2 km buffer: likely DEM sinks and model artefacts. Modes for flood cells inside them:
  flag  set them to -1 (default)
  mask  drop them all (JRC's "use with caution")
  deep  drop only cells deeper than 10 m, keep shallower floodplain
  keep  ignore the spurious layer

Single Int16 band: depth in centimetres (>= 0), -1 flagged, -255 nodata (dry or
permanent water). Half the size of the source Float32 and plenty precise. Tiles go to
data/temp/glofas_<RP>_<mode>/ (existing ones are skipped, so re-running resumes),
mosaicked by data/temp/glofas_<RP>_<mode>.vrt (tiles share one 3" grid).

Needs data/temp/glofas_<RP>/, glofas_Permanent_WaterBodies/ and
glofas_Spurious_Depths/ from download_glofas.py.

Usage: python scripts/build_glofas_mosaic.py [RP] [MODE] [WORKERS]
       python scripts/build_glofas_mosaic.py RP500 flag 6
"""
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

gdal.UseExceptions()

TEMP = Path(__file__).parent.parent / "data" / "temp"
SRC_NODATA = -9999.0
NODATA = -255
FLAG = -1
DEEP_M = 10.0
MODES = ("flag", "mask", "deep", "keep")


def read_mask(path):
    ds = gdal.Open(str(path))  # keep a ref, or the band outlives it
    return ds.GetRasterBand(1).ReadAsArray() == 1


def write_tile(out_path, like, cm):
    tmp = out_path.with_name(out_path.name + ".part")
    dst = gdal.GetDriverByName("GTiff").Create(
        str(tmp), like.RasterXSize, like.RasterYSize, 1, gdal.GDT_Int16,
        ["COMPRESS=ZSTD", "PREDICTOR=2", "TILED=YES"],
    )
    dst.SetGeoTransform(like.GetGeoTransform())
    dst.SetProjection(like.GetProjection())
    band = dst.GetRasterBand(1)
    band.SetNoDataValue(NODATA)
    band.SetDescription("depth_cm")
    band.WriteArray(cm)
    dst = None
    tmp.replace(out_path)


def clean_tile(args):
    depth_path, water_path, spurious_path, out_path, mode = args
    if out_path.exists():
        return 0, 0, 0
    src = gdal.Open(str(depth_path))
    depth = src.GetRasterBand(1).ReadAsArray()
    wet = depth != SRC_NODATA
    water = wet & read_mask(water_path)
    flood = wet & ~water
    spurious = np.zeros_like(flood)
    if mode != "keep":
        spurious = flood & read_mask(spurious_path)
        if mode == "deep":
            spurious &= depth > DEEP_M
    cm = np.full(depth.shape, NODATA, np.int16)
    cm[flood] = np.minimum(np.round(depth[flood] * 100), np.iinfo(np.int16).max)
    cm[spurious] = FLAG if mode == "flag" else NODATA
    write_tile(out_path, src, cm)
    return int(wet.sum()), int(water.sum()), int(spurious.sum())


def main():
    rp = sys.argv[1] if len(sys.argv) > 1 else "RP500"
    mode = sys.argv[2] if len(sys.argv) > 2 else "flag"
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    assert mode in MODES, f"mode must be one of {MODES}"

    in_dir = TEMP / f"glofas_{rp}"
    water_dir = TEMP / "glofas_Permanent_WaterBodies"
    spurious_dir = TEMP / "glofas_Spurious_Depths"
    out_dir = TEMP / f"glofas_{rp}_{mode}"
    out_dir.mkdir(parents=True, exist_ok=True)

    jobs = []
    for depth_path in sorted(in_dir.glob(f"*_{rp}_depth.tif")):
        tile = depth_path.name.replace(f"_{rp}_depth.tif", "")
        water_path = water_dir / f"{tile}_permanent_water.tif"
        spurious_path = spurious_dir / f"{tile}_spurious_depth_areas.tif"
        for p in (water_path, spurious_path):
            if not p.exists():
                sys.exit(f"missing {p}")
        jobs.append((depth_path, water_path, spurious_path, out_dir / depth_path.name, mode))
    print(f"{len(jobs)} tiles, mode={mode} -> {out_dir}", flush=True)

    t0 = time.time()
    wet_total = water_total = spurious_total = 0
    with Pool(workers) as pool:
        for i, (wet, water, spurious) in enumerate(pool.imap_unordered(clean_tile, jobs), 1):
            wet_total += wet
            water_total += water
            spurious_total += spurious
            print(f"\r[{i}/{len(jobs)}]", end="", flush=True)
    print(f"\nProcessed in {time.time() - t0:.0f}s")
    if wet_total:
        verb = "flagged" if mode == "flag" else "dropped"
        print(f"Of {wet_total:,} wet cells in newly processed tiles: "
              f"{water_total:,} permanent water removed ({water_total / wet_total:.1%}), "
              f"{spurious_total:,} spurious {verb} ({spurious_total / wet_total:.1%})")

    tiles = sorted(str(p) for p in out_dir.glob("*.tif"))
    vrt_path = TEMP / f"glofas_{rp}_{mode}.vrt"
    gdal.BuildVRT(str(vrt_path), tiles)
    ds = gdal.Open(str(vrt_path))
    print(f"Mosaic {vrt_path} ({ds.RasterXSize} x {ds.RasterYSize}, {len(tiles)} tiles)")


if __name__ == "__main__":
    main()
