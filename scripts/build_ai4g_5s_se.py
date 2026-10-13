"""Reduces the AI4G flood dataset (Microsoft AI for Good, Sentinel-1, 2014-2024) to 5",
for a box around Mississippi, North Carolina, Florida and West Virginia only.

A copy of build_ai4g_30s.py, limited to BBOX (the four states' bounds extended by at
least BUFFER_KM in every direction), at 5" instead of 30" and with the flooded share (in
%) instead of classes. Each 3x3 degree tile's 240m-buffer.tif (0.6", values 0 no flood,
1 exclusion mask, 2 flooded at any time) that intersects BBOX is downloaded into memory,
never to disk, and reduced to 5" Float32, one band:
  0..100  % of the cell's pixels flooded (value 2)
  101     MASKED: any of the cell's pixels is in the exclusion mask
  -255    NODATA: no tile
5" is 8 1/3 source pixels, so the blocks do not nest exactly: each source pixel goes to
the 5" cell containing its centre (rows and columns of 8 or 9 pixels, none counted
twice). Each reduced tile (2160x2160) goes to data/temp/ai4g_5s_se/; existing ones are
skipped, so re-running resumes. Some files download as 0 bytes although the Hugging Face
page lists them with a size (a known LFS problem, dataset discussion #3): they get a
.empty marker instead, count as no tile and are listed at the end. At the end the tiles
are clipped to BBOX in one GeoTIFF, data/temp/ai4g_5s_se.tif.

--mosaic downloads nothing: it writes the tiles done so far into that GeoTIFF (or
OUT.tif), replacing it. It can run while a download does.

Usage: python scripts/build_ai4g_5s_se.py [WORKERS]
       python scripts/build_ai4g_5s_se.py --mosaic [OUT.tif]
"""
import json
import math
import os
import re
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
OUT_DIR = TEMP / "ai4g_5s_se"
MOSAIC = TEMP / "ai4g_5s_se.tif"
# West, south, east, north of Mississippi, Florida, North Carolina and West Virginia
# together (US Census state bounds): Mississippi's west edge, the Florida Keys, Cape
# Hatteras, West Virginia's north edge.
STATES = (-91.655, 24.396, -75.400, 40.638)
BUFFER_KM = 100
CELLS = 2160  # 5" cells per 3 degree tile
NODATA = -255
MASKED = 101


def extended_bbox(bounds, km):
    """bounds extended by at least km on every side, rounded outward to 0.1 degree.

    A degree of latitude is at least 110.57 km; a degree of longitude is shortest at the
    northern edge, so that latitude sets the longitude margin for the whole box."""
    w, s, e, n = bounds
    dlat = km / 110.57
    dlon = km / (111.32 * math.cos(math.radians(max(abs(s), abs(n)))))
    return (math.floor((w - dlon) * 10) / 10, math.floor((s - dlat) * 10) / 10,
            math.ceil((e + dlon) * 10) / 10, math.ceil((n + dlat) * 10) / 10)


BBOX = extended_bbox(STATES, BUFFER_KM)  # (-92.9, 23.4, -74.2, 41.6), on the 5" grid


def tile_bounds(path):
    """(west, south) of a tile from its name, e.g. N30W090 -> (-90, 30)."""
    ns, lat, ew, lon = re.search(r"([NS])(\d\d)([EW])(\d{3})-240m", path).groups()
    return (-int(lon) if ew == "W" else int(lon)), (-int(lat) if ns == "S" else int(lat))


def intersects(path):
    w, s = tile_bounds(path)
    return w < BBOX[2] and w + 3 > BBOX[0] and s < BBOX[3] and s + 3 > BBOX[1]


def list_tiles():
    """Paths of the 240m-buffer.tif files intersecting BBOX, from the Hugging Face tree API."""
    first = math.floor(BBOX[1] / 3) * 3
    bands = [f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}" for lat in range(first, math.ceil(BBOX[3]), 3)]
    paths = []
    for band in bands:
        url = f"{API}/{band}?recursive=true"
        while url:
            resp = urllib.request.urlopen(url, timeout=60)
            paths += [e["path"] for e in json.load(resp)
                      if e["type"] == "file" and e["path"].endswith("-240m-buffer.tif")
                      and intersects(e["path"])]
            link = resp.headers.get("Link") or ""
            url = link.split("<")[1].split(">")[0] if 'rel="next"' in link else None
    return sorted(paths)


class EmptyFile(Exception):
    """The file downloads as 0 bytes."""


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


def block_values(a):
    """CELLS x CELLS cell values of a square tile, each pixel in the cell holding its
    centre: MASKED where any pixel is 1, else the % of pixels that are 2."""
    n = a.shape[0]
    # first pixel whose centre (j + 0.5) is in cell i: j >= i * n / CELLS - 0.5
    starts = np.ceil(np.arange(CELLS) * n / CELLS - 0.5).astype(np.intp)
    sizes = np.diff(np.append(starts, n))
    flooded = np.zeros((CELLS, CELLS), np.float32)
    masked = np.zeros((CELLS, CELLS), bool)
    for i, (y, h) in enumerate(zip(starts, sizes)):  # strips keep memory low
        rows = a[y:y + h]
        flooded[i] = np.add.reduceat((rows == 2).sum(axis=0, dtype=np.int32), starts)
        masked[i] = np.logical_or.reduceat((rows == 1).any(axis=0), starts)
    flooded *= 100 / np.outer(sizes, sizes).astype(np.float32)
    return np.where(masked, np.float32(MASKED), flooded)


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
            return path, "EMPTY: downloads as 0 bytes"
        except Exception as e:
            if attempt == retries:
                return path, f"FAILED: {e}"
            time.sleep(30 * attempt)  # long enough for a rate limit to lift
    if a.shape[0] != a.shape[1] or abs(gt[1] * a.shape[1] - 3) > 1e-9:
        return path, f"FAILED: size {a.shape[1]}x{a.shape[0]}, pixel {gt[1]}"
    values = block_values(a)

    tmp = out.with_name(out.name + ".part")
    dst = gdal.GetDriverByName("GTiff").Create(
        str(tmp), CELLS, CELLS, 1, gdal.GDT_Float32, ["COMPRESS=DEFLATE", "TILED=YES"])
    dst.SetGeoTransform((gt[0], 3 / CELLS, 0, gt[3], 0, -3 / CELLS))
    dst.SetProjection(proj)
    band = dst.GetRasterBand(1)
    band.SetNoDataValue(NODATA)
    band.WriteArray(values)
    dst = None
    tmp.replace(out)
    return path, (f"ok ({size / 1e6:.1f} MB, {int(((values > 0) & (values != MASKED)).sum())} cells with flooding, "
                  f"{int((values == MASKED).sum())} masked)")


def mosaic(out):
    """One 5" GeoTIFF of BBOX from the reduced tiles done so far."""
    tiles = sorted(str(p) for p in OUT_DIR.glob("*.tif"))
    vrt = gdal.BuildVRT("", tiles, outputBounds=BBOX, srcNodata=NODATA, VRTNodata=NODATA)
    tmp = out.with_name(out.name + ".part")
    gdal.Translate(str(tmp), vrt, format="GTiff", noData=NODATA,
                   creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=3", "BIGTIFF=IF_SAFER"])
    vrt = None
    tmp.replace(out)
    return len(tiles)


def main():
    if sys.argv[1:2] == ["--mosaic"]:
        out = Path(sys.argv[2]) if len(sys.argv) > 2 else MOSAIC
        n = mosaic(out)
        print(f"{out}: {n} tiles, {out.stat().st_size / 1e6:.1f} MB")
        return
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 3  # ~0.5 GB each
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = list_tiles()
    print(f"BBOX {BBOX}: {len(paths)} tiles -> {OUT_DIR}", flush=True)

    t0 = time.time()
    failed, empty = [], []
    with Pool(workers) as pool:
        for i, (path, status) in enumerate(pool.imap_unordered(reduce_tile, paths), 1):
            if status.startswith("FAILED"):
                failed.append(path)
            elif status.startswith("EMPTY"):
                empty.append(path)
            print(f"[{i}/{len(paths)}] {path} {status}", flush=True)
    print(f"Done in {time.time() - t0:.0f}s")

    n = mosaic(MOSAIC)
    ds = gdal.Open(str(MOSAIC))
    print(f"Mosaic {MOSAIC} ({ds.RasterXSize} x {ds.RasterYSize}, {n} tiles)")
    if empty:
        print(f"{len(empty)} downloaded as 0 bytes, no data there: {', '.join(empty)}")
    if failed:
        print(f"{len(failed)} failed (re-run to retry): {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
