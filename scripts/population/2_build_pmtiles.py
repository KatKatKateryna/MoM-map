"""Population step 2: GHS-POP as value PMTiles (the map's "Population" layer).

Extracts the GeoTIFF from the zip of 1_download.py into data/temp/population/ (once;
reading it inside the Deflate zip would re-inflate from the start on every seek), then
deletes the zip: only the GeoTIFF is kept, and 1_download.py skips it from then on. Each zip
holds one GeoTIFF (Float64, nodata -200, World Mollweide), its .tif.ovr overviews, a PDF
and an .xlsx; only the GeoTIFF is extracted (262 MB at 1 km, 6.5 GB at 100 m). The Mollweide
cells are equal-area, so people per cell is a density, and the warp to Web Mercator
averages it as one: lower zooms show the mean density of the valid cells under each
pixel (0 included). Cells with 0 people are not drawn.

  1000 m  (default) people per 1 km2 cell = people per km2, tiled up to zoom 7 like the
          other ~1 km layers, the whole world:
          data/persistent/population_1km/population_1km.pmtiles
  100 m   --100m: the 100 m file, multiplied by 100 to people per km2 so it shares the
          1 km layer's colour ramp and slider. Tiled up to MAXZOOM (default 10, ~150 m
          pixels at the equator, about the source); the whole world at that zoom is far
          too large for this host, so give --bbox WEST,SOUTH,EAST,NORTH (degrees):
          data/persistent/population_100m/population_100m.pmtiles
The Web Mercator raster is kept as data/temp/population/<name>_3857.tif.

Usage: python scripts/population/2_build_pmtiles.py
       python scripts/population/2_build_pmtiles.py --100m --bbox -92,24,-75,41 [--maxzoom 10]
"""
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
import layer_pmtiles as lp  # sets PROJ_LIB for GDAL before importing it
from osgeo import gdal

WORK = lp.TEMP / "population"
ZIP = "GHS_POP_E2025_GLOBE_R2023A_54009_{res}_V1_0.zip"
# GHS-POP's nodata, used if the file does not declare one
GHS_NODATA = -200


def extract_tif(zip_path):
    """The zip's one GeoTIFF, extracted next to it (named like the zip); the zip is then
    deleted. If the GeoTIFF is already there, the zip is not needed."""
    out = zip_path.with_suffix(".tif")
    if out.exists():  # written as .part and renamed, so complete
        return out
    if not zip_path.exists():
        sys.exit(f"missing {zip_path}: run scripts/population/1_download.py"
                 f"{' --100m' if '_100_' in zip_path.name else ''} first")
    with zipfile.ZipFile(zip_path) as z:
        tifs = [i for i in z.infolist() if i.filename.lower().endswith(".tif")]
        if len(tifs) != 1:
            sys.exit(f"{zip_path}: expected one .tif, found {[i.filename for i in tifs]}")
        info = tifs[0]
        print(f"extracting {info.filename} ({info.file_size / 1e9:.1f} GB)", flush=True)
        tmp = out.with_name(out.name + ".part")
        with z.open(info) as f, open(tmp, "wb") as g:
            shutil.copyfileobj(f, g, 1 << 24)
    if tmp.stat().st_size != info.file_size:
        sys.exit(f"{tmp}: extracted {tmp.stat().st_size} bytes, expected {info.file_size}")
    tmp.replace(out)
    zip_path.unlink()
    print(f"deleted {zip_path}", flush=True)
    return out


def main():
    args = sys.argv[1:]
    fine = "--100m" in args
    bbox, maxzoom = None, lp.MAXZOOM
    if fine:
        args.remove("--100m")
        maxzoom = 10
    for flag in ("--bbox", "--maxzoom"):
        if flag in args:
            k = args.index(flag)
            if flag == "--bbox":
                bbox = tuple(map(float, args[k + 1].split(",")))
            else:
                maxzoom = int(args[k + 1])
            del args[k:k + 2]
    if args or (bbox is not None and len(bbox) != 4):
        sys.exit(__doc__)

    src = extract_tif(WORK / ZIP.format(res=100 if fine else 1000))
    nodata = gdal.Open(str(src)).GetRasterBand(1).GetNoDataValue()
    print(f"{src}: nodata {nodata}", flush=True)
    name = "population_100m" if fine else "population_1km"
    title = "Population (100 m, people per km2)" if fine else "Population (1 km)"
    lp.build_layer(src, WORK, name, name, title,
                   src_nodata=GHS_NODATA if nodata is None else nodata,
                   maxzoom=maxzoom, bbox=bbox, scale=100 if fine else 1)


if __name__ == "__main__":
    main()
