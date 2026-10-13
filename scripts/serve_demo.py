"""Builds the demo pages and serves the site laid out as deployed, for checking locally.

The demo pages (sub_pages/demo_*/index.html, built by .github/actions/build-demo-pages)
have <base href="../">: deployed, they sit one level below the site root. This serves a
temporary folder of that layout (symlinks to the repo's index.html, css/, js/, assets/,
data/, plus each demo_*/index.html) with serve.py's Range support, which PMTiles needs.

data/tiles/ (the MoM watershed snapshots) is built by CI and not in the repo; with
--tiles-from it is fetched from a deployed copy instead (Range requests passed through).

Usage: python scripts/serve_demo.py [PORT] [HOST] [--tiles-from URL]   (default 8080 on 127.0.0.1)
       e.g. --tiles-from https://katkatkateryna.github.io/MoM-map/data/tiles/
       then open http://HOST:PORT/demo_202610/
"""
import functools
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent.parent


def build_site():
    """Builds the demo pages; returns a temporary folder laid out like the deployed site."""
    subprocess.run([sys.executable, ".github/actions/build-demo-pages/build_demo_pages.py"],
                   cwd=ROOT, check=True)
    site = Path(tempfile.mkdtemp(prefix="mom_site_"))
    for name in ("index.html", "css", "js", "assets", "data"):
        os.symlink(ROOT / name, site / name)
    for page in (ROOT / "sub_pages").glob("demo_*/index.html"):
        (site / page.parent.name).mkdir()
        shutil.copy(page, site / page.parent.name / "index.html")
    return site


def proxy_get(handler, url):
    """Answers a GET with url's response, passing the Range header through."""
    headers = {"User-Agent": "MoM-map serve_demo"}
    if handler.headers.get("Range"):
        headers["Range"] = handler.headers["Range"]
    try:
        resp = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60)
    except urllib.error.HTTPError as e:
        resp = e
    body = resp.read()
    handler.send_response(resp.status if hasattr(resp, "status") else resp.code)
    for name in ("Content-Type", "Content-Range", "Accept-Ranges", "ETag", "Last-Modified"):
        if resp.headers.get(name):
            handler.send_header(name, resp.headers[name])
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def start_server(site, port=0, host="127.0.0.1", quiet=True, tiles_from=None):
    """Serves site in a background thread; returns the server (server_address has the port).
    With tiles_from, data/tiles/ requests go to that URL."""
    spec = importlib.util.spec_from_file_location("serve", ROOT / "serve.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def do_GET(self):
        path = self.path.split("?")[0]
        if tiles_from and path.startswith("/data/tiles/"):
            return proxy_get(self, tiles_from.rstrip("/") + "/" + path[len("/data/tiles/"):])
        return mod.RangeHandler.do_GET(self)

    handler = type("Handler", (mod.RangeHandler,), {"do_GET": do_GET})
    if quiet:
        handler.log_message = lambda *a: None
    server = ThreadingHTTPServer((host, port), functools.partial(handler, directory=str(site)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    args = sys.argv[1:]
    tiles_from = None
    if "--tiles-from" in args:
        k = args.index("--tiles-from")
        tiles_from = args[k + 1]
        del args[k:k + 2]
    port = int(args[0]) if args else 8080
    host = args[1] if len(args) > 1 else "127.0.0.1"
    site = build_site()
    server = start_server(site, port, host, quiet=False, tiles_from=tiles_from)
    print(f"Serving {site} at http://{host}:{port}/  (demo pages: /demo_*/)", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        shutil.rmtree(site, ignore_errors=True)
