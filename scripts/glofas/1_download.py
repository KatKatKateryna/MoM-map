"""GloFAS step 1: downloads the CEMS-GLOFAS flood hazard tiles (JRC open data).

Every file of each directory listing under BASE_URL goes to data/temp/glofas/<DIR>/.
The flood layers need three: RP500 (depth tiles), Permanent_WaterBodies and
Spurious_Depths (step 2 masks with them). Files already present with the server's size
are skipped, so re-running resumes an interrupted download.

Usage: python scripts/glofas/1_download.py [DIR ...] [--workers N]
       (default: RP500 Permanent_WaterBodies Spurious_Depths, 8 workers)
"""
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
from download import download

BASE_URL = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/CEMS-GLOFAS/flood_hazard/{dir}/"
OUT = Path(__file__).resolve().parents[2] / "data" / "temp" / "glofas"
DIRS = ["RP500", "Permanent_WaterBodies", "Spurious_Depths"]


def list_files(url):
    with urllib.request.urlopen(url, timeout=60) as resp:
        html = resp.read().decode("utf-8", "replace")
    # skip sort links (?C=...), parent dir and subdirectories
    names = re.findall(r'href="([^"?/][^"]*)"', html)
    return [n for n in dict.fromkeys(names) if not n.endswith("/")]


def download_dir(name, workers):
    url = BASE_URL.format(dir=name)
    out_dir = OUT / name
    names = list_files(url)
    print(f"{len(names)} files in {url} -> {out_dir}")

    t0 = time.time()
    total = 0
    failed = []
    with ThreadPoolExecutor(workers) as pool:
        futures = {pool.submit(download, url + n, out_dir / n): n for n in names}
        for i, fut in enumerate(as_completed(futures), 1):
            n = futures[fut]
            status, size = fut.result()
            total += size
            if status.startswith("FAILED"):
                failed.append(n)
            print(f"[{i}/{len(names)}] {n} {status} ({size / 1e6:.1f} MB)", flush=True)
    print(f"{name}: done in {time.time() - t0:.0f}s, {total / 1e9:.2f} GB")
    return failed


def main():
    args = sys.argv[1:]
    workers = 8
    if "--workers" in args:
        k = args.index("--workers")
        workers = int(args[k + 1])
        del args[k:k + 2]
    failed = []
    for name in args or DIRS:
        failed += download_dir(name, workers)
    if failed:
        print(f"{len(failed)} failed (re-run to retry): {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
