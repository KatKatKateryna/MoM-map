"""Builds 30" (~1 km) agriculture layers from ESA WorldCereal 2021 v100 (10 m, Zenodo 7875105).

WorldCereal 2021 v100, (c) ESA WorldCereal Consortium, CC BY 4.0 (https://zenodo.org/records/7875105).
Each product is a zip of ~106 GeoTIFFs, one per agro-ecological zone (AEZ), at 0.3" in
EPSG:4326. Values: 100 = the class (crop, irrigated, ...), 0 = not the class, 254 = not
cropland (crop type and irrigation products), 255 = no data. The zips (7-20 GB each) are
never downloaded whole: each GeoTIFF is fetched alone (remote_zip.py) and kept in
data/temp/worldcereal/downloads/ until pipeline.sh has built and verified the tiles.

Stages (each resumes where it stopped, so re-running is safe):

  counts   for every AEZ file of every product, the number of class pixels (100) and of
           valid pixels (not 255) in each 30" cell of the global grid (the population grid:
           -180..180, -90..90, 1/120 degree). Each pixel goes to the cell holding its center
           (pixels are 1/100 of a cell). Full resolution: the files' overviews are mode
           resampled and undercount small fields (-1% at 2x, -9% at 8x). Output:
           data/temp/worldcereal/counts/<product>/<aez>.npz
  mosaic   per category, the share (%) of each 30" cell's valid area in the class, summing
           the counts of overlapping AEZs. Categories of several products (maize main and
           second season; active irrigation and active cropland in the winter cereals and
           both maize seasons) take the largest share. Cells with valid data but no class
           hold 0, so lower zooms average the share over all land.
           Output: data/temp/worldcereal/<category>_30s.tif
  tiles    value PMTiles (common/build_value_pmtiles.py) up to zoom 7 like population_1km:
           data/persistent/worldcereal/worldcereal_<category>_30s.pmtiles

Kept in data/temp/worldcereal/ for later use: the 30" counts and the 30" rasters.
The 10 m downloads are deleted by pipeline.sh once the tiles are verified
(a counts run from scratch, ~156 GB, does not fit on this disk: the download disk check
fails files when it is full), and the Web Mercator intermediates once tiled.

Usage: python scripts/worldcereal/build_30s.py [counts [WORKERS]] [mosaic] [tiles] [CATEGORY ...]
           [--aez ID,ID] [--partial]                                               (default: all)
  --aez      counts only these AEZs (a sample, e.g. 46173: the US corn belt)
  --partial  mosaics the counts done so far instead of stopping when some are missing
  --window LON0,LAT0,LON1,LAT1
             a quick sample: counts, mosaics and tiles only this box, all in
             data/temp/worldcereal_sample/ (never mixed with the full counts); the PMTiles
             go to data/persistent/worldcereal/ as worldcereal_<category>_30s_sample.pmtiles
"""
import json
import os
import shutil
import sys
import time
import traceback
import multiprocessing
from multiprocessing.connection import wait
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
import build_value_pmtiles as bvp  # sets PROJ_LIB for GDAL before importing it
import remote_zip
from osgeo import gdal
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

gdal.UseExceptions()

ROOT = Path(__file__).resolve().parents[2]
TEMP = ROOT / "data" / "temp" / "worldcereal"
MEMBERS = TEMP  # zip listings, shared by the sample
COUNTS = TEMP / "counts"
DOWNLOADS = TEMP / "downloads"
# Sample box in 30" cells (x0, y0, x1, y1) from --window, or None for the whole world
WINDOW = None
OUT_DIR = ROOT / "data" / "persistent" / "worldcereal"
ZIP_URL = "https://zenodo.org/records/7875105/files/WorldCereal_2021_{}_classification.zip"
CELLS_PER_DEG = 120  # 30"
WIDTH, HEIGHT = 360 * CELLS_PER_DEG, 180 * CELLS_PER_DEG
NODATA = -9999.0
MAXZOOM = 7
# Cells need this many valid 10 m pixels (1% of a full cell) to get a share
MIN_VALID = 100
# The counts stage does not start with less free disk than this
MIN_FREE_DISK_START = 3e9
# Each download needs its own size free plus this, else that file fails without downloading
# (other processes on the disk keep some room, even with downloads in flight)
DOWNLOAD_DISK_RESERVE = 1e9
# Pixels per read when counting: whole 1024 x 1024 blocks, so each is decompressed once,
# in pieces small enough for the widest AEZs (1.4M pixels) on this 4 GB machine
READ_CHUNK_PIXELS = 1 << 24
CREATE = ["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=3", "BIGTIFF=YES", "SPARSE_OK=TRUE"]

# Category -> (products, title), in the dataset's own names. Products of several seasons
# are combined (the larger share per cell)
CATEGORIES = {
    "temporarycrops": (["tc-annual_temporarycrops"],
                       "WorldCereal 2021 temporary crops, share of cell (%)"),
    "maize": (["tc-maize-main_maize", "tc-maize-second_maize"],
              "WorldCereal 2021 maize, main or second season, share of cell (%)"),
    "wintercereals": (["tc-wintercereals_wintercereals"],
                      "WorldCereal 2021 winter cereals, share of cell (%)"),
    "springcereals": (["tc-springcereals_springcereals"],
                      "WorldCereal 2021 spring cereals, share of cell (%)"),
    "irrigation": (["tc-wintercereals_irrigation", "tc-maize-main_irrigation",
                    "tc-maize-second_irrigation"],
                   "WorldCereal 2021 active irrigation, any season, share of cell (%)"),
    "activecropland": (["tc-wintercereals_activecropland", "tc-maize-main_activecropland",
                        "tc-maize-second_activecropland"],
                       "WorldCereal 2021 active cropland, any season, share of cell (%)"),
}
PRODUCTS = [p for products, _ in CATEGORIES.values() for p in products]


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def members(product):
    """{aez id: (zip member name, member info)}, cached in TEMP."""
    cache = MEMBERS / f"members_{product}.json"
    if not cache.exists():
        m = remote_zip.list_members(ZIP_URL.format(product))
        tifs = {k.split("/")[-1].split("_")[0]: (k, v) for k, v in m.items() if k.endswith(".tif")}
        cache.write_text(json.dumps(tifs))
    return {k: (name, tuple(info)) for k, (name, info) in json.loads(cache.read_text()).items()}


def count_cells(path):
    """Class (100) and valid (not 255) pixel counts per 30" cell of one AEZ file; with
    --window, only the window."""
    ds = gdal.Open(str(path))
    band = ds.GetRasterBand(1)
    w, h = ds.RasterXSize, ds.RasterYSize
    x0, dx, rx, y0, ry, dy = ds.GetGeoTransform()
    assert rx == 0 and ry == 0 and dy < 0, "rotated grid"
    # The 30" cell of each pixel center
    ox = np.floor((x0 + (np.arange(w) + 0.5) * dx + 180) * CELLS_PER_DEG).astype(np.int64)
    oy = np.floor((90 - (y0 + (np.arange(h) + 0.5) * dy)) * CELLS_PER_DEG).astype(np.int64)
    c0, c1, r0_, r1_ = 0, w, 0, h
    if WINDOW:  # only the pixels in the sample box
        cs = np.flatnonzero((ox >= WINDOW[0]) & (ox < WINDOW[2]))
        rs = np.flatnonzero((oy >= WINDOW[1]) & (oy < WINDOW[3]))
        if not len(cs) or not len(rs):
            return None
        c0, c1, r0_, r1_ = cs[0], cs[-1] + 1, rs[0], rs[-1] + 1
        ox, oy = ox[c0:c1], oy[r0_:r1_]
    oy0, ox0 = int(oy[0]), int(ox[0])
    dtype = np.uint16  # a cell holds at most 101^2 pixels
    crop = np.zeros((int(oy[-1]) - oy0 + 1, int(ox[-1]) - ox0 + 1), dtype)
    valid = np.zeros_like(crop)
    # Whole blocks, a row of blocks at a time in column chunks: strips of one cell row
    # decompress every block once per strip, and a full row of blocks of the widest AEZs
    # takes 1.4 GB. Cells cut by a chunk edge get the counts of both chunks
    bw, bh = band.GetBlockSize()
    cw = max(bw, READ_CHUNK_PIXELS // bh // bw * bw)
    for by in range(r0_ - r0_ % bh, r1_, bh):
        ra, rb = max(by, r0_), min(by + bh, r1_)
        rows = oy[ra - r0_:rb - r0_] - oy0
        row_starts = np.r_[0, np.flatnonzero(np.diff(rows)) + 1, len(rows)]
        for bx in range(c0 - c0 % bw, c1, cw):
            ca, cb = max(bx, c0), min(bx + cw, c1)
            a = band.ReadAsArray(int(ca), int(ra), int(cb - ca), int(rb - ra))
            if (a == 255).all():  # most of a big AEZ's bounding box
                continue
            cols = ox[ca - c0:cb - c0] - ox0
            col_starts = np.r_[0, np.flatnonzero(np.diff(cols)) + 1]
            cells = np.s_[cols[0]:cols[-1] + 1]
            # Per cell row, as count_nonzero over axis 0 is many times faster than reduceat
            for r0, r1 in zip(row_starts[:-1], row_starts[1:]):
                s = a[r0:r1]
                v = np.count_nonzero(s != 255, axis=0)
                if not v.any():
                    continue
                valid[rows[r0], cells] += np.add.reduceat(v, col_starts).astype(dtype)
                crop[rows[r0], cells] += np.add.reduceat(np.count_nonzero(s == 100, axis=0),
                                                         col_starts).astype(dtype)
    return oy0, ox0, crop, valid


def job_done(product, aez):
    return (COUNTS / product / f"{aez}.npz").exists()


def count_job(job):
    product, aez, name, info = job
    out = COUNTS / product / f"{aez}.npz"
    if job_done(product, aez):
        return job, "skipped"
    url = ZIP_URL.format(product)
    tif = DOWNLOADS / f"{product}_{aez}.tif"
    try:
        t = time.time()
        # A complete file left by a stopped run is used as is
        if not (tif.exists() and tif.stat().st_size == info[2]):
            tif.unlink(missing_ok=True)
            free = shutil.disk_usage(DOWNLOADS).free
            if free < info[2] + DOWNLOAD_DISK_RESERVE:
                return job, (f"FAILED: not enough disk for the download ({info[2] / 1e9:.2f} GB"
                             f" + {DOWNLOAD_DISK_RESERVE / 1e9:.0f} GB reserve, {free / 1e9:.2f} GB free)")
            remote_zip.fetch_member(url, info, tif)
        t_dl = time.time() - t
        counted = count_cells(tif)
        if counted is None:  # --window: the file is outside the sample box
            np.savez_compressed(out, empty=True)
            return job, "outside the window"
        oy0, ox0, crop, valid = counted
        tmp = out.with_name(out.stem + ".part.npz")
        np.savez_compressed(tmp, oy0=oy0, ox0=ox0, crop=crop, valid=valid)
        tmp.replace(out)
        share = crop.sum() / max(valid.sum(), 1) * 100
        return job, (f"ok ({info[1] / 1e6:.0f} MB in {t_dl:.0f}s, counted in {time.time() - t - t_dl:.0f}s, "
                     f"{share:.2f}% class)")
    except Exception as e:
        return job, f"FAILED: {e!r}\n{traceback.format_exc()}"


def count_job_to(job, send):
    """count_job in a worker process, its status sent back through the pipe."""
    send.send(count_job(job)[1])
    send.close()


def stage_counts(workers, aez_ids=None):
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    jobs = []
    for product in PRODUCTS:  # category order: temporary crops first
        (COUNTS / product).mkdir(parents=True, exist_ok=True)
        ms = members(product)
        todo = [(product, aez, name, info) for aez, (name, info) in ms.items()
                if not job_done(product, aez) and (not aez_ids or aez in aez_ids)]
        log(f"{product}: {len(ms)} AEZ files, {len(todo)} to do, "
            f"{sum(j[3][1] for j in todo) / 1e9:.1f} GB to download")
        jobs += todo
    free = shutil.disk_usage(DOWNLOADS).free
    if jobs and free < MIN_FREE_DISK_START:
        raise SystemExit(f"only {free / 1e9:.2f} GB free on disk, the counts stage needs at least "
                         f"{MIN_FREE_DISK_START / 1e9:.0f} GB to start")
    gdal.SetConfigOption("GDAL_CACHEMAX", "256")
    # One process per file (frees its memory), up to `workers` at once. Not a Pool: a
    # worker killed by the kernel (out of memory) loses its result there, and the run waits
    # for it forever; here its pipe closes and the file counts as failed
    ctx = multiprocessing.get_context("fork")  # the workers keep main()'s globals
    running = {}  # result pipe -> (job, process)
    failed = i = 0
    total = len(jobs)
    while jobs or running:
        while jobs and len(running) < workers:
            job = jobs.pop(0)
            recv, send = ctx.Pipe(duplex=False)
            p = ctx.Process(target=count_job_to, args=(job, send), daemon=True)
            p.start()
            send.close()
            running[recv] = (job, p)
        for recv in wait(list(running)):
            job, p = running.pop(recv)
            try:
                status = recv.recv()
            except EOFError:
                status = None
            recv.close()
            p.join()
            if status is None:  # its download, if complete, is reused by the next run
                status =f"FAILED: worker died (exit code {p.exitcode}" + \
                         (", killed: out of memory?)" if p.exitcode == -9 else ")")
            i += 1
            failed += status.startswith("FAILED")
            log(f"[{i}/{total}] {job[0]} {job[1]}: {status}")
    if failed:
        raise SystemExit(f"{failed} AEZ files failed; re-run to retry them")


def stage_mosaic(categories, partial=False):
    band_rows = 540  # 4.5 degrees of latitude per pass (~1 GB of arrays at most)
    for cat in categories:
        products, title = CATEGORIES[cat]
        tiles = {p: [np.load(f) for f in sorted((COUNTS / p).glob("*.npz"))
                     if not f.name.endswith(".part.npz")] for p in products}
        expected_files = {p: len(tiles[p]) for p in products}
        tiles = {p: [t for t in ts if "empty" not in t] for p, ts in tiles.items()}
        expected = {p: len(members(p)) for p in products}
        missing = {p: expected[p] - expected_files[p] for p in products if expected[p] != expected_files[p]}
        if missing and partial:
            log(f"{cat}: partial mosaic, counts missing {missing}")
        elif missing:
            raise SystemExit(f"{cat}: counts missing {missing}; run the counts stage first")
        out = TEMP / f"{cat}_30s.tif"
        tmp = out.with_name(out.name + ".part")
        # The whole world, or the sample box
        gx0, gy0, gx1, gy1 = WINDOW or (0, 0, WIDTH, HEIGHT)
        gw = gx1 - gx0
        dst = gdal.GetDriverByName("GTiff").Create(str(tmp), gw, gy1 - gy0, 1, gdal.GDT_Float32, CREATE)
        dst.SetGeoTransform((-180 + gx0 / CELLS_PER_DEG, 1 / CELLS_PER_DEG, 0,
                             90 - gy0 / CELLS_PER_DEG, 0, -1 / CELLS_PER_DEG))
        dst.SetProjection("EPSG:4326")
        out_band = dst.GetRasterBand(1)
        out_band.SetNoDataValue(NODATA)
        extent = [WIDTH, HEIGHT, 0, 0]
        for y0 in range(gy0, gy1, band_rows):
            y1 = min(y0 + band_rows, gy1)
            best = np.full((y1 - y0, gw), NODATA, np.float32)
            for p in products:
                crop = np.zeros((y1 - y0, gw), np.uint32)
                valid = np.zeros_like(crop)
                for t in tiles[p]:
                    ty0, tx0 = int(t["oy0"]), int(t["ox0"])
                    c, v = t["crop"], t["valid"]
                    a0, a1 = max(ty0, y0), min(ty0 + c.shape[0], y1)
                    if a0 >= a1:
                        continue
                    # Files reaching past the antimeridian wrap around
                    cols = (tx0 + np.arange(c.shape[1])) % WIDTH - gx0
                    keep = (cols >= 0) & (cols < gw)
                    if not keep.any():
                        continue
                    crop[a0 - y0:a1 - y0][:, cols[keep]] += c[a0 - ty0:a1 - ty0][:, keep]
                    valid[a0 - y0:a1 - y0][:, cols[keep]] += v[a0 - ty0:a1 - ty0][:, keep]
                ok = valid >= MIN_VALID
                share = np.where(ok, crop / np.maximum(valid, 1) * 100, NODATA).astype(np.float32)
                best = np.maximum(best, share)
            out_band.WriteArray(best, 0, y0 - gy0)
            rows, cols = np.nonzero((best != NODATA).any(axis=1))[0], np.nonzero((best != NODATA).any(axis=0))[0]
            if len(rows):
                extent = [min(extent[0], gx0 + cols[0]), min(extent[1], y0 + rows[0]),
                          max(extent[2], gx0 + cols[-1] + 1), max(extent[3], y0 + rows[-1] + 1)]
        dst = None
        tmp.replace(out)
        # Data extent in cells (x0, y0, x1, y1), for the tiles stage; null when empty
        (TEMP / f"{cat}_30s.json").write_text(json.dumps(
            {"extent": [int(v) for v in extent] if extent[2] > extent[0] else None}))
        log(f"{cat}: {out}")


def stage_tiles(categories):
    # The warp buffer, GDAL's block cache and the tiling must fit in this host's 3.9 GB:
    # with 1 GB each the warp was killed for lack of memory
    gdal.SetConfigOption("GDAL_CACHEMAX", "256")
    work = TEMP / "tiles_work"
    work.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for cat in categories:
        title = CATEGORIES[cat][1]
        src = TEMP / f"{cat}_30s.tif"
        merc = work / f"{cat}_3857.tif"
        out = OUT_DIR / f"worldcereal_{cat}_30s{'_sample' if WINDOW else ''}.pmtiles"
        # Resumes: an archive newer than its 30" raster is already done (it is moved into
        # place only when complete)
        if out.exists() and out.stat().st_mtime > src.stat().st_mtime:
            log(f"{cat}: {out.name} newer than {src.name}, skipped")
            continue
        log(f"{cat}: Web Mercator")
        # Zoom 8 pixel size, then build() averages the shares (zeros included) per zoom
        # Only the data's extent (plus a cell), not the whole world
        extent = json.loads((TEMP / f"{cat}_30s.json").read_text())["extent"]
        if extent is None:
            # Only in a sample without this category: an archive with no tiles, so the
            # map still loads the other categories
            log(f"{cat}: no data, empty archive")
            with open(out, "wb") as f:
                writer = Writer(f)
                # The writer needs a tile: one of no data (index 0) at zoom 0
                writer.write_tile(zxy_to_tileid(0, 0, 0), bvp.encode_tile(np.zeros((bvp.TILE, bvp.TILE))))
                writer.finalize({"tile_type": TileType.WEBP, "tile_compression": Compression.NONE,
                                 "min_zoom": 0, "max_zoom": MAXZOOM, "min_lon_e7": 0, "min_lat_e7": 0,
                                 "max_lon_e7": 0, "max_lat_e7": 0, "center_zoom": 0,
                                 "center_lon_e7": 0, "center_lat_e7": 0},
                                {"name": title, "encoding": "terrarium",
                                 "value_scale": {"type": "log", "ratio": bvp.STEP_RATIO, "offset": bvp.STEP_OFFSET}})
            continue
        x0, y0, x1, y1 = extent
        lon0, lon1 = -180 + (x0 - 1) / CELLS_PER_DEG, -180 + (x1 + 1) / CELLS_PER_DEG
        lat1, lat0 = min(90 - (y0 - 1) / CELLS_PER_DEG, 85.05), max(90 - (y1 + 1) / CELLS_PER_DEG, -85.05)
        res = bvp.tile_size_m(MAXZOOM + 1) / bvp.TILE
        gdal.Warp(str(merc), str(src), dstSRS="EPSG:3857", xRes=res, yRes=res,
                  outputBounds=(max(lon0, -180), lat0, min(lon1, 180), lat1), outputBoundsSRS="EPSG:4326",
                  srcNodata=NODATA, dstNodata=NODATA, resampleAlg="average", targetAlignedPixels=True,
                  multithread=True, warpOptions=["NUM_THREADS=ALL_CPUS"], warpMemoryLimit=256,
                  creationOptions=CREATE)
        log(f"{cat}: tiles")
        tmp = work / out.name
        bvp.build(str(merc), str(tmp), MAXZOOM, title, str(work / f"{cat}_levels"))
        shutil.move(str(tmp), str(out))
        shutil.rmtree(work / f"{cat}_levels")
        # Only a reprojection of the 30" raster, large, and the disk is short
        merc.unlink()
    shutil.rmtree(work, ignore_errors=True)


def main():
    args = sys.argv[1:]
    aez_ids = None
    if "--aez" in args:
        k = args.index("--aez")
        aez_ids = set(args[k + 1].split(","))
        del args[k:k + 2]
    if "--window" in args:
        global WINDOW, TEMP, COUNTS, DOWNLOADS
        k = args.index("--window")
        lon0, lat0, lon1, lat1 = map(float, args[k + 1].split(","))
        WINDOW = (round((lon0 + 180) * CELLS_PER_DEG), round((90 - lat1) * CELLS_PER_DEG),
                  round((lon1 + 180) * CELLS_PER_DEG), round((90 - lat0) * CELLS_PER_DEG))
        del args[k:k + 2]
        TEMP = ROOT / "data" / "temp" / "worldcereal_sample"
        COUNTS, DOWNLOADS = TEMP / "counts", TEMP / "downloads"
        args.append("--partial")
    partial = "--partial" in args
    args = [a for a in args if a != "--partial"]
    if not any(a in ("counts", "mosaic", "tiles") for a in args):
        args += ["counts", "mosaic", "tiles"]
    TEMP.mkdir(parents=True, exist_ok=True)
    cats = [a for a in args if a in CATEGORIES] or list(CATEGORIES)
    if "counts" in args:
        k = args.index("counts")
        workers = int(args[k + 1]) if k + 1 < len(args) and args[k + 1].isdigit() else 3
        stage_counts(workers, aez_ids)
    if "mosaic" in args:
        stage_mosaic(cats, partial)
    if "tiles" in args:
        stage_tiles(cats)


if __name__ == "__main__":
    main()
