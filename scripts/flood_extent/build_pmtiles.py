"""Flood extent: combines the 30" Aqueduct and GloFAS depth rasters into two layers.

  combined  flood_max_1km: the deepest of Aqueduct riverine, Aqueduct coastal (1-in-1000
            years) and GloFAS (1-in-500 years), per cell, in metres
  extent    flood_extent_1km: 1 wherever that maximum is above 0 (the map's
            "Flood extent, any model" layer)

Needs the 30" rasters of scripts/aqueduct/2_build_pmtiles.py and
scripts/glofas/3_build_pmtiles.py. Output, per layer:
  data/temp/flood_extent/<name>_4326.tif
  data/persistent/flood_max/<name>.pmtiles

Usage: python scripts/flood_extent/build_pmtiles.py [combined] [extent]   (default: both)
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
import layer_pmtiles as lp  # sets PROJ_LIB for GDAL before importing it
from osgeo import gdal

WORK = lp.TEMP / "flood_extent"
SOURCES = [
    (lp.TEMP / "aqueduct" / "aqueduct_coastal_rp1000_4326.tif", "scripts/aqueduct/"),
    (lp.TEMP / "aqueduct" / "aqueduct_riverine_rp1000_4326.tif", "scripts/aqueduct/"),
    (lp.TEMP / "glofas" / "glofas_rp500_1km_4326.tif", "scripts/glofas/"),
]
LAYERS = {
    "combined": ("flood_max_1km", "Flood depth, deepest of Aqueduct riverine, coastal and GloFAS (m)"),
    "extent": ("flood_extent_1km", "Flooded in Aqueduct riverine, coastal or GloFAS (1)"),
}


def combine_max(srcs, out, extent=False):
    """Per-cell maximum of rasters on one grid, ignoring nodata, in row strips;
    with extent, 1 wherever that maximum is above 0."""
    dss = [gdal.Open(str(p)) for p in srcs]
    first = dss[0]
    for ds in dss[1:]:
        assert (ds.RasterXSize, ds.RasterYSize) == (first.RasterXSize, first.RasterYSize) and \
            np.allclose(ds.GetGeoTransform(), first.GetGeoTransform(), rtol=0, atol=1e-9), "grids differ"
    dst = gdal.GetDriverByName("GTiff").Create(
        str(out), first.RasterXSize, first.RasterYSize, 1, gdal.GDT_Float32, lp.CREATE)
    dst.SetGeoTransform(first.GetGeoTransform())
    dst.SetProjection(first.GetProjection())
    out_band = dst.GetRasterBand(1)
    out_band.SetNoDataValue(lp.NODATA)
    for y in range(0, first.RasterYSize, 512):
        rows = min(512, first.RasterYSize - y)
        # NODATA is below any depth, so it only survives where every layer is dry
        a = np.maximum.reduce([ds.GetRasterBand(1).ReadAsArray(0, y, first.RasterXSize, rows)
                               for ds in dss])
        if extent:
            a = np.where(a > 0, 1, lp.NODATA).astype(np.float32)
        out_band.WriteArray(a, 0, y)
    dst = None


def main():
    layers = sys.argv[1:] or list(LAYERS)
    if any(k not in LAYERS for k in layers):
        sys.exit(__doc__)
    for path, step in SOURCES:
        if not path.exists():
            sys.exit(f"missing {path}: run {step} first")
    WORK.mkdir(parents=True, exist_ok=True)
    for key in layers:
        name, title = LAYERS[key]
        merged = WORK / f"{name}_4326.tif"
        print(f"{name}: per-cell max of {len(SOURCES)} layers", flush=True)
        tmp = merged.with_name(merged.name + ".part")
        combine_max([p for p, _ in SOURCES], tmp, extent=key == "extent")
        tmp.replace(merged)
        lp.build_layer(merged, WORK, "flood_max", name, title)


if __name__ == "__main__":
    main()
