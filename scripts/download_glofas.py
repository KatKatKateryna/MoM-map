"""Downloads every file of a CEMS-GLOFAS flood hazard directory listing (JRC open data).

Files already present with the server's size are skipped, so re-running resumes
an interrupted download. Partial files are written as *.part and renamed when done.

Usage: python scripts/download_glofas.py [RP] [WORKERS]
       python scripts/download_glofas.py RP500 8   ->  data/temp/glofas_RP500/
"""
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE_URL = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/CEMS-GLOFAS/flood_hazard/{rp}/"


def list_files(url):
    with urllib.request.urlopen(url, timeout=60) as resp:
        html = resp.read().decode("utf-8", "replace")
    # skip sort links (?C=...), parent dir and subdirectories
    names = re.findall(r'href="([^"?/][^"]*)"', html)
    return [n for n in dict.fromkeys(names) if not n.endswith("/")]


def download(url, dest, retries=3):
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                size = int(resp.headers.get("Content-Length", -1))
                if dest.exists() and dest.stat().st_size == size:
                    return "skipped", size
                part = dest.with_name(dest.name + ".part")
                with open(part, "wb") as f:
                    while chunk := resp.read(1 << 20):
                        f.write(chunk)
            if size >= 0 and part.stat().st_size != size:
                raise IOError(f"size mismatch {part.stat().st_size} != {size}")
            part.replace(dest)
            return "ok", size
        except Exception as e:
            if attempt == retries:
                return f"FAILED: {e}", 0
            time.sleep(2 * attempt)


def main():
    rp = sys.argv[1] if len(sys.argv) > 1 else "RP500"
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    url = BASE_URL.format(rp=rp)
    out_dir = Path(__file__).parent.parent / "data" / "temp" / f"glofas_{rp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    names = list_files(url)
    print(f"{len(names)} files in {url} -> {out_dir}")

    t0 = time.time()
    total = 0
    failed = []
    with ThreadPoolExecutor(workers) as pool:
        futures = {pool.submit(download, url + n, out_dir / n): n for n in names}
        for i, fut in enumerate(as_completed(futures), 1):
            name = futures[fut]
            status, size = fut.result()
            total += size
            if status.startswith("FAILED"):
                failed.append(name)
            print(f"[{i}/{len(names)}] {name} {status} ({size / 1e6:.1f} MB)", flush=True)

    print(f"Done in {time.time() - t0:.0f}s, {total / 1e9:.2f} GB")
    if failed:
        print(f"{len(failed)} failed (re-run to retry): {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
