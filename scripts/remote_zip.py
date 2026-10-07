"""Reads single members of a large remote zip (e.g. on Zenodo) without downloading it all.

The central directory at the end of the zip is read with a few HTTP range requests, then
each member is fetched with one range request of exactly its bytes and inflated on the
fly to a local file. One request per member keeps clear of per-IP rate limits (429) that
GDAL's /vsizip//vsicurl/ trips with its many small reads.
"""
import struct
import time
import urllib.error
import urllib.request
import zlib


def _get(url, start, end, retries=8, stream=False):
    """Bytes start..end (inclusive) of url, retrying rate limits and network errors."""
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}",
                                                       "User-Agent": "MoM-map/worldcereal"})
            resp = urllib.request.urlopen(req, timeout=300)
            if resp.status != 206:
                raise IOError(f"no range support (HTTP {resp.status})")
            return resp if stream else resp.read()
        except (urllib.error.URLError, IOError, TimeoutError) as e:
            if attempt == retries:
                raise
            wait = 60 * attempt if getattr(e, "code", None) == 429 else 15 * attempt
            print(f"    {e}; retry {attempt} in {wait}s", flush=True)
            time.sleep(wait)


def size_of(url):
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "MoM-map/worldcereal"})
    return int(urllib.request.urlopen(req, timeout=120).headers["Content-Length"])


def list_members(url):
    """{name: (local header offset, compressed size, uncompressed size, method)} (Zip64 aware)."""
    total = size_of(url)
    tail = _get(url, max(0, total - 65536 - 22), total - 1)
    eocd = tail.rfind(b"PK\x05\x06")
    if eocd < 0:
        raise IOError("no end of central directory")
    count, cd_size, cd_off = struct.unpack("<HII", tail[eocd + 10:eocd + 20])
    loc = tail.rfind(b"PK\x06\x07")  # Zip64 locator
    if loc >= 0:
        (z64_off,) = struct.unpack("<Q", tail[loc + 8:loc + 16])
        rec = _get(url, z64_off, z64_off + 55)
        count, cd_size, cd_off = struct.unpack("<QQQ", rec[32:56])
    cd = _get(url, cd_off, cd_off + cd_size - 1)
    members, p = {}, 0
    for _ in range(count):
        # Central directory file header fields (zip spec 4.3.12)
        method, = struct.unpack("<H", cd[p + 10:p + 12])
        csize, usize = struct.unpack("<II", cd[p + 20:p + 28])
        nlen, xlen, clen = struct.unpack("<HHH", cd[p + 28:p + 34])
        off, = struct.unpack("<I", cd[p + 42:p + 46])
        name = cd[p + 46:p + 46 + nlen].decode()
        extra = cd[p + 46 + nlen:p + 46 + nlen + xlen]
        # Zip64 extra field: the 0xFFFFFFFF fields, in order usize, csize, offset
        q = 0
        while q + 4 <= len(extra):
            tag, ln = struct.unpack("<HH", extra[q:q + 4])
            if tag == 1:
                vals = list(struct.unpack(f"<{ln // 8}Q", extra[q + 4:q + 4 + ln // 8 * 8]))
                if usize == 0xFFFFFFFF: usize = vals.pop(0)
                if csize == 0xFFFFFFFF: csize = vals.pop(0)
                if off == 0xFFFFFFFF: off = vals.pop(0)
            q += 4 + ln
        members[name] = (off, csize, usize, method)
        p += 46 + nlen + xlen + clen
    return members


def fetch_member(url, info, out_path, retries=5):
    """Writes one member, inflated, to out_path."""
    off, csize, usize, method = info
    for attempt in range(1, retries + 1):
        try:
            head = _get(url, off, off + 29)
            nlen, xlen = struct.unpack("<HH", head[26:30])
            start = off + 30 + nlen + xlen
            resp = _get(url, start, start + csize - 1, stream=True)
            inflater = zlib.decompressobj(-15) if method == 8 else None
            written = 0
            with open(out_path, "wb") as f:
                while chunk := resp.read(1 << 22):
                    data = inflater.decompress(chunk) if inflater else chunk
                    f.write(data)
                    written += len(data)
                if inflater:
                    data = inflater.flush()
                    f.write(data)
                    written += len(data)
            if written != usize:
                raise IOError(f"got {written} of {usize} bytes")
            return
        except Exception as e:
            if attempt == retries:
                raise
            print(f"    fetch failed ({e}); retry {attempt}", flush=True)
            time.sleep(30 * attempt)
