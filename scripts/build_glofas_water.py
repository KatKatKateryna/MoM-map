"""Merges the GloFAS permanent water body tiles (JRC CEMS) into one global GeoTIFF.

Value 1 = permanent water (rivers, lakes), nodata 255 elsewhere, 3" (~90 m) WGS84.
Input data/temp/glofas_Permanent_WaterBodies/ from download_glofas.py.

Usage: python scripts/build_glofas_water.py [OUT.tif]
       default out: data/temp/glofas_permanent_water.tif
"""
import os
import sys
import time
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

from osgeo import gdal

gdal.UseExceptions()
gdal.SetConfigOption("GDAL_CACHEMAX", "2048")
gdal.SetConfigOption("GDAL_NUM_THREADS", "ALL_CPUS")

TEMP = Path(__file__).parent.parent / "data" / "temp"
NODATA = 255


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else TEMP / "glofas_permanent_water.tif"
    tiles = sorted(str(p) for p in (TEMP / "glofas_Permanent_WaterBodies").glob("*_permanent_water.tif"))
    print(f"{len(tiles)} tiles -> {out}", flush=True)

    t0 = time.time()
    vrt = gdal.BuildVRT("", tiles, srcNodata=NODATA, VRTNodata=NODATA)
    tmp = out.with_name(out.name + ".part")
    gdal.Translate(
        str(tmp), vrt, format="GTiff",
        creationOptions=["COMPRESS=ZSTD", "TILED=YES", "BLOCKXSIZE=512",
                         "BLOCKYSIZE=512", "BIGTIFF=YES", "SPARSE_OK=TRUE"],
        callback=gdal.TermProgress_nocb,
    )
    vrt = None
    tmp.replace(out)
    ds = gdal.Open(str(out))
    ds.GetRasterBand(1).SetDescription("permanent_water")
    print(f"Done in {time.time() - t0:.0f}s: {ds.RasterXSize} x {ds.RasterYSize}, "
          f"{out.stat().st_size / 2**20:.0f} MB")


if __name__ == "__main__":
    main()
