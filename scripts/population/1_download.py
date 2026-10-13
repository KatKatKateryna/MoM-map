"""Population step 1: downloads GHS-POP (JRC Global Human Settlement Layer, R2023A).

GHS_POP_E2025_GLOBE_R2023A_54009_<RES> V1-0: residential population estimated for 2025,
in World Mollweide (ESRI:54009), people per cell. Two resolutions:
  1000 m  the map's Population layer (default)
  100 m   --100m, the alternative when finer detail is needed (a much larger file)
The zip goes to data/temp/population/; it is skipped if already present with the
server's size, or if 2_build_pmtiles.py already extracted its GeoTIFF (and deleted it).

Usage: python scripts/population/1_download.py [--100m]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
from download import download

URL = ("https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/"
       "GHS_POP_E2025_GLOBE_R2023A_54009_{res}/V1-0/GHS_POP_E2025_GLOBE_R2023A_54009_{res}_V1_0.zip")
OUT = Path(__file__).resolve().parents[2] / "data" / "temp" / "population"


def main():
    if sys.argv[1:] not in ([], ["--100m"]):
        sys.exit(__doc__)
    url = URL.format(res=100 if sys.argv[1:] else 1000)
    out = OUT / url.rsplit("/", 1)[1]
    tif = out.with_suffix(".tif")  # the zip's GeoTIFF has the zip's name
    if tif.exists():
        print(f"{tif} already extracted, nothing to download", flush=True)
        return
    status, size = download(url, out)
    print(f"{out} {status} ({size / 1e6:.1f} MB)", flush=True)
    if status.startswith("FAILED"):
        sys.exit(1)


if __name__ == "__main__":
    main()
