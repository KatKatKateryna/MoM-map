"""Builds value PMTiles (build_value_pmtiles.py) of flood depth layers in metres.

  aqueduct  the WRI Aqueduct rasters in data/temp/aqueduct/ (30", ~1 km, metres)
  glofas    the GloFAS depth mosaic data/temp/glofas_RP500_flag_depth.vrt (3", UInt16 cm,
            65535 nodata; permanent water removed, cells flagged as spurious kept),
            first averaged onto the Aqueduct 30" grid. The average only counts flood
            cells, so a 1 km cell holds the mean depth of its flooded 90 m cells.
  combined  the deepest of the three 30" layers above, per cell
  extent    1 wherever combined is flooded (depth > 0)

Dry cells (0 in Aqueduct) count as nodata too, so lower zooms average flooded cells
only, as for GloFAS. Each layer is warped to Web Mercator at zoom 8 resolution, then
tiled up to MAXZOOM 7 like population_1km. Output: data/persistent/<layer>/<name>.pmtiles.
Intermediate rasters go to data/temp/flood_work/, removed at the end.

Usage: python scripts/build_flood_pmtiles.py [aqueduct] [glofas] [combined] [extent]   (default: all)
"""
import os
import re
import shutil
import sys
import time
from pathlib import Path

import numpy as np

import build_value_pmtiles as bvp  # sets PROJ_LIB for GDAL before importing it
from osgeo import gdal

ROOT = Path(__file__).parent.parent
TEMP = ROOT / "data" / "temp"
WORK = TEMP / "flood_work"
PERSISTENT = ROOT / "data" / "persistent"
NODATA = -9999.0
MAXZOOM = 7
CREATE = ["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=3", "BIGTIFF=YES", "SPARSE_OK=TRUE"]

AQUEDUCT = [
    ("inuncoast_historical_wtsub_hist_rp1000_0.tif", "aqueduct_coastal_rp1000",
     "Aqueduct coastal flood depth, RP1000 (m)"),
    ("inunriver_historical_000000000WATCH_1980_rp01000.tif", "aqueduct_riverine_rp1000",
     "Aqueduct riverine flood depth, RP1000 (m)"),
]
GLOFAS = ("glofas_rp500_1km", "GloFAS river flood depth, RP500, 1 km mean (m)")
COMBINED = ("flood_max_1km", "Flood depth, deepest of Aqueduct riverine, coastal and GloFAS (m)")
EXTENT = ("flood_extent_1km", "Flooded in Aqueduct riverine, coastal or GloFAS (1)")
GLOFAS_VRT = TEMP / "glofas_RP500_flag_depth.vrt"
GLOFAS_GRID = TEMP / "aqueduct" / AQUEDUCT[1][0]  # 30" target grid


def dry_to_nodata(src, out):
    """Copy with 0 (dry) and negative cells set to nodata, in row strips."""
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
        a = band.ReadAsArray(0, y, ds.RasterXSize, rows)
        a[~(a > 0)] = NODATA
        out_band.WriteArray(a, 0, y)
    dst = None


def glofas_to_30s(out):
    """GloFAS averaged onto the 30" grid, metres, ignoring nodata."""
    # A lookup table maps cm to m; 65535 is source nodata. Cached statistics and the
    # scale (in cm) would be wrong for the metre band, so they go
    xml = GLOFAS_VRT.read_text(encoding="utf-8")
    xml = re.sub(r"\s*<Metadata>.*?</Metadata>|\s*<Histograms>.*?</Histograms>|\s*<Scale>.*?</Scale>",
                 "", xml, flags=re.S)
    xml = xml.replace('dataType="UInt16" band="1"', 'dataType="Float32" band="1"', 1)
    xml = re.sub(r"<NoDataValue>65535</NoDataValue>", f"<NoDataValue>{NODATA:g}</NoDataValue>", xml, 1)
    xml = xml.replace("<NODATA>65535</NODATA>", "<NODATA>65535</NODATA><LUT>0:0,65534:655.34</LUT>")
    lut_vrt = TEMP / "glofas_RP500_flag_depth_m.vrt"  # next to the tiles: relative paths
    lut_vrt.write_text(xml, encoding="utf-8")

    grid = gdal.Open(str(GLOFAS_GRID))
    gt = grid.GetGeoTransform()
    bounds = (gt[0], gt[3] + gt[5] * grid.RasterYSize, gt[0] + gt[1] * grid.RasterXSize, gt[3])
    tmp = out.with_name(out.name + ".part")
    gdal.Warp(str(tmp), str(lut_vrt), format="GTiff", outputBounds=bounds,
              width=grid.RasterXSize, height=grid.RasterYSize, outputType=gdal.GDT_Float32,
              srcNodata=NODATA, dstNodata=NODATA, resampleAlg="average",
              multithread=True, warpOptions=["NUM_THREADS=ALL_CPUS"],
              warpMemoryLimit=2048, creationOptions=CREATE, callback=gdal.TermProgress_nocb)
    tmp.replace(out)


def to_mercator(src, out, resample="average"):
    """Web Mercator at the zoom-8 pixel size, averaging valid cells (or their maximum)."""
    res = bvp.tile_size_m(MAXZOOM + 1) / bvp.TILE
    gdal.Warp(str(out), str(src), dstSRS="EPSG:3857", xRes=res, yRes=res,
              outputBounds=(-bvp.HALF_WORLD, -bvp.HALF_WORLD, bvp.HALF_WORLD, bvp.HALF_WORLD),
              srcNodata=NODATA, dstNodata=NODATA, resampleAlg=resample, targetAlignedPixels=True,
              multithread=True, warpOptions=["NUM_THREADS=ALL_CPUS"], warpMemoryLimit=2048,
              creationOptions=CREATE, callback=gdal.TermProgress_nocb)


def combine_max(srcs, out, extent=False):
    """Per-cell maximum of rasters on one grid, ignoring nodata, in row strips;
    with extent, 1 wherever that maximum is above 0."""
    dss = [gdal.Open(str(p)) for p in srcs]
    first = dss[0]
    for ds in dss[1:]:
        assert (ds.RasterXSize, ds.RasterYSize) == (first.RasterXSize, first.RasterYSize) and \
            np.allclose(ds.GetGeoTransform(), first.GetGeoTransform(), rtol=0, atol=1e-9), "grids differ"
    dst = gdal.GetDriverByName("GTiff").Create(
        str(out), first.RasterXSize, first.RasterYSize, 1, gdal.GDT_Float32, CREATE)
    dst.SetGeoTransform(first.GetGeoTransform())
    dst.SetProjection(first.GetProjection())
    out_band = dst.GetRasterBand(1)
    out_band.SetNoDataValue(NODATA)
    for y in range(0, first.RasterYSize, 512):
        rows = min(512, first.RasterYSize - y)
        # NODATA is below any depth, so it only survives where every layer is dry
        a = np.maximum.reduce([ds.GetRasterBand(1).ReadAsArray(0, y, first.RasterXSize, rows)
                               for ds in dss])
        if extent:
            a = np.where(a > 0, 1, NODATA).astype(np.float32)
        out_band.WriteArray(a, 0, y)
    dst = None


def build_layer(src_4326, folder, name, title, resample="average"):
    t0 = time.time()
    merc = WORK / f"{name}_3857.tif"
    print(f"{name}: Web Mercator", flush=True)
    to_mercator(src_4326, merc, resample)
    out_dir = PERSISTENT / folder
    out_dir.mkdir(parents=True, exist_ok=True)
    work = WORK / f"{name}_levels"
    print(f"{name}: tiles", flush=True)
    bvp.build(str(merc), str(out_dir / f"{name}.pmtiles"), MAXZOOM, title, str(work), resample)
    shutil.rmtree(work)
    merc.unlink()
    print(f"{name}: done in {time.time() - t0:.0f}s", flush=True)


def aqueduct_30s(src, name):
    out = WORK / f"{name}_4326.tif"
    if not out.exists():
        print(f"{name}: dry -> nodata", flush=True)
        dry_to_nodata(TEMP / "aqueduct" / src, out)
    return out


def glofas_30s():
    out = WORK / f"{GLOFAS[0]}_4326.tif"
    if not out.exists():
        print(f"{GLOFAS[0]}: averaging to 30\"", flush=True)
        glofas_to_30s(out)
    return out


def main():
    layers = sys.argv[1:] or ["aqueduct", "glofas", "combined", "extent"]
    WORK.mkdir(parents=True, exist_ok=True)
    if "aqueduct" in layers:
        for src, name, title in AQUEDUCT:
            build_layer(aqueduct_30s(src, name), "aqueduct", name, title)
    if "glofas" in layers:
        build_layer(glofas_30s(), "glofas", *GLOFAS)
    for key, (name, title) in (("combined", COMBINED), ("extent", EXTENT)):
        if key not in layers:
            continue
        srcs = [aqueduct_30s(src, n) for src, n, _ in AQUEDUCT] + [glofas_30s()]
        merged = WORK / f"{name}_4326.tif"
        print(f"{name}: per-cell max of {len(srcs)} layers", flush=True)
        combine_max(srcs, merged, extent=key == "extent")
        build_layer(merged, "flood_max", name, title)
    shutil.rmtree(WORK)
    (TEMP / "glofas_RP500_flag_depth_m.vrt").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
