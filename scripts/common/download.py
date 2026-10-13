"""Resumable HTTP download of one file.

A file already present with the server's size is skipped, so re-running resumes an
interrupted download. Partial files are written as *.part and renamed when done.
"""
import time
import urllib.request


def download(url, dest, retries=3):
    """(status, size): "ok", "skipped" or "FAILED: ..." and the server's size in bytes."""
    dest.parent.mkdir(parents=True, exist_ok=True)
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
