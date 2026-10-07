"""Reduces the AI4G flood dataset (Microsoft AI for Good, Sentinel-1, 2014-2024) to 30".

Each 3x3 degree tile's 240m-buffer.tif (0.6", values 0 no flood, 1 exclusion mask,
2 flooded at any time) is downloaded into memory, never to disk, and reduced to the
30" Aqueduct grid by the maximum of each 50x50 block: 2 if any pixel was flooded,
else 1 if any is masked, else 0. Tiles start on whole degrees, so the blocks nest
exactly. Each reduced tile (360x360) goes to data/temp/ai4g_30s/; existing ones are
skipped, so re-running resumes. Some files are empty (0 bytes) on the server: they get
a .empty marker instead and count as no tile. data/temp/ai4g_30s.vrt mosaics the tiles
(255 = no tile).

--mosaic downloads nothing: it writes the tiles done so far into one global 30" GeoTIFF,
data/temp/ai4g_30s.tif (0, 1, 2; 255 = no tile yet). It can run while a download does.

Usage: python scripts/build_ai4g_30s.py [WORKERS]
       python scripts/build_ai4g_30s.py --mosaic
"""
import json
import os
import sys
import time
import urllib.request
from multiprocessing import Pool
from pathlib import Path

import numpy as np

import build_value_pmtiles  # noqa: F401  sets PROJ_LIB for GDAL before importing it
from osgeo import gdal

gdal.UseExceptions()

REPO = "ai-for-good-lab/ai4g-flood-dataset"
API = f"https://huggingface.co/api/datasets/{REPO}/tree/main"
FILE_URL = f"https://huggingface.co/datasets/{REPO}/resolve/main/"
TEMP = Path(__file__).parent.parent / "data" / "temp"
OUT_DIR = TEMP / "ai4g_30s"
CELLS = 360  # 30" cells per 3 degree tile
NODATA = 255


def list_tiles():
    """Paths of all 240m-buffer.tif files, from the Hugging Face tree API."""
    top = json.load(urllib.request.urlopen(API, timeout=60))
    paths = []
    for band in (d["path"] for d in top if d["type"] == "directory" and d["path"] != "paper_plot_tifs"):
        url = f"{API}/{band}?recursive=true"
        while url:
            resp = urllib.request.urlopen(url, timeout=60)
            paths += [e["path"] for e in json.load(resp)
                      if e["type"] == "file" and e["path"].endswith("-240m-buffer.tif")]
            link = resp.headers.get("Link") or ""
            url = link.split("<")[1].split(">")[0] if 'rel="next"' in link else None
    return sorted(paths)


class EmptyFile(Exception):
    """The file exists on the server but has no content."""


def read_tile(path):
    """Downloads a tile into memory and returns (array, geotransform, projection, bytes)."""
    resp = urllib.request.urlopen(FILE_URL + path, timeout=300)
    data = resp.read()
    if not data and resp.headers.get("Content-Length") == "0":
        raise EmptyFile()
    mem = f"/vsimem/{os.getpid()}.tif"
    gdal.FileFromMemBuffer(mem, data)
    try:
        try:
            ds = gdal.Open(mem)
        except RuntimeError:
            # e.g. an error or rate-limit page instead of the file
            raise IOError(f"not a GeoTIFF ({len(data)} bytes): {data[:120]!r}")
        a = ds.GetRasterBand(1).ReadAsArray()
        return a, ds.GetGeoTransform(), ds.GetProjection(), len(data)
    finally:
        ds = None
        gdal.Unlink(mem)


def reduce_tile(path, retries=5):
    out = OUT_DIR / path.split("/")[-1].replace("-240m-buffer.tif", ".tif")
    empty = out.with_suffix(".empty")
    if out.exists() or empty.exists():
        return path, "skipped"
    for attempt in range(1, retries + 1):
        try:
            a, gt, proj, size = read_tile(path)
            break
        except EmptyFile:
            empty.touch()
            return path, "empty on the server (0 bytes)"
        except Exception as e:
            if attempt == retries:
                return path, f"FAILED: {e}"
            time.sleep(30 * attempt)  # long enough for a rate limit to lift
    if a.shape[0] != a.shape[1] or a.shape[0] % CELLS:
        return path, f"FAILED: size {a.shape[1]}x{a.shape[0]}"
    f = a.shape[0] // CELLS
    reduced = a.reshape(CELLS, f, CELLS, f).max(axis=(1, 3))

    tmp = out.with_name(out.name + ".part")
    dst = gdal.GetDriverByName("GTiff").Create(
        str(tmp), CELLS, CELLS, 1, gdal.GDT_Byte, ["COMPRESS=DEFLATE", "TILED=YES"])
    dst.SetGeoTransform((gt[0], 3 / CELLS, 0, gt[3], 0, -3 / CELLS))
    dst.SetProjection(proj)
    band = dst.GetRasterBand(1)
    band.SetNoDataValue(NODATA)
    band.WriteArray(reduced)
    dst = None
    tmp.replace(out)
    return path, f"ok ({size / 1e6:.1f} MB, {int((reduced == 2).sum())} flooded cells)"


def mosaic(out):
    """One global 30" GeoTIFF of the reduced tiles done so far."""
    tiles = sorted(str(p) for p in OUT_DIR.glob("*.tif"))
    vrt = gdal.BuildVRT("", tiles, outputBounds=(-180, -90, 180, 90),
                        srcNodata=NODATA, VRTNodata=NODATA)
    tmp = out.with_name(out.name + ".part")
    gdal.Translate(str(tmp), vrt, format="GTiff", noData=NODATA,
                   creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=2",
                                    "BIGTIFF=YES", "SPARSE_OK=TRUE"])
    vrt = None
    tmp.replace(out)
    return len(tiles)


def main():
    if sys.argv[1:] == ["--mosaic"]:
        out = TEMP / "ai4g_30s.tif"
        n = mosaic(out)
        print(f"{out}: {n} tiles, {out.stat().st_size / 1e6:.1f} MB")
        return
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = list_tiles()
    print(f"{len(paths)} tiles -> {OUT_DIR}", flush=True)

    t0 = time.time()
    failed = []
    with Pool(workers) as pool:
        for i, (path, status) in enumerate(pool.imap_unordered(reduce_tile, paths), 1):
            if status.startswith("FAILED"):
                failed.append(path)
            print(f"[{i}/{len(paths)}] {path} {status}", flush=True)
    print(f"Done in {time.time() - t0:.0f}s")

    tiles = sorted(str(p) for p in OUT_DIR.glob("*.tif"))
    vrt = TEMP / "ai4g_30s.vrt"
    gdal.BuildVRT(str(vrt), tiles, srcNodata=NODATA, VRTNodata=NODATA)
    ds = gdal.Open(str(vrt))
    print(f"Mosaic {vrt} ({ds.RasterXSize} x {ds.RasterYSize}, {len(tiles)} tiles)")
    if failed:
        print(f"{len(failed)} failed (re-run to retry): {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
