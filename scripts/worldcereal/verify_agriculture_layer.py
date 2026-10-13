"""Checks the WorldCereal agriculture layer on the built demo_202610 page in headless Chromium.

Builds the demo pages (.github/actions/build-demo-pages/build_demo_pages.py), serves them
laid out as deployed (each demo page one level down from the site root) and checks that:
  - the "Agriculture (WorldCereal 2021)" legend entry loads, starts hidden and has a
    checkbox per category;
  - its layers are drawn below the Population layer;
  - turning it on over the US corn belt adds dark green pixels, and the category
    checkboxes switch their own layers;
  - no errors are logged.
Screenshots go to data/temp/worldcereal/verify_*.png. Exits 1 on any failure.

Needs Playwright with Chromium: the mom-layers env (environment-layers.yml,
scripts/setup_layers_env.sh), with PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright-mom.
Usage: python scripts/worldcereal/verify_agriculture_layer.py [DEMO]
"""
import base64
import os
import shutil
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import serve_demo

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "temp" / "worldcereal"
TITLE = "Agriculture (WorldCereal 2021)"
CATEGORIES = 6
TILES_FROM = os.environ.get("MOM_TILES_FROM", "https://katkatkateryna.github.io/MoM-map/data/tiles/")


# Green pixels of a screenshot, counted in the page (no image library needed)
COUNT_GREEN = """async (b64) => {
  const img = new Image();
  img.src = 'data:image/png;base64,' + b64;
  await img.decode();
  const c = document.createElement('canvas');
  c.width = img.width; c.height = img.height;
  const ctx = c.getContext('2d');
  ctx.drawImage(img, 0, 0);
  const d = ctx.getImageData(0, 0, c.width, c.height).data;
  let n = 0;
  for (let k = 0; k < d.length; k += 4) {
    const [r, g, b] = [d[k], d[k + 1], d[k + 2]];
    if (g > r + 20 && g >= b && g < 190) n++;
  }
  return n / (d.length / 4);
}"""


def main():
    demo = sys.argv[1] if len(sys.argv) > 1 else "demo_202610"
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/root/.cache/ms-playwright-mom")
    failures, errors = [], []

    def check(ok, msg):
        print(("ok    " if ok else "FAIL  ") + msg, flush=True)
        if not ok:
            failures.append(msg)

    site = serve_demo.build_site()
    try:
        # data/tiles/ (MoM watersheds, built by CI) from the deployed site
        server = serve_demo.start_server(site, tiles_from=TILES_FROM)
        url = f"http://127.0.0.1:{server.server_address[1]}/{demo}/index.html"

        with sync_playwright() as p:
            # Running as root: no sandbox; WebGL through SwiftShader (no GPU)
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-gpu-sandbox",
                                              "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.on("console", lambda m: m.type in ("error", "warning") and errors.append(f"{m.type}: {m.text}"))
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.goto(url)
            heading = page.locator("label.legend-toggle", has_text=TITLE)
            heading.wait_for(timeout=60000)
            section = page.locator("section.side-panel__section", has=heading)
            master = heading.locator("input")
            boxes = section.locator("label.legend-category input")
            # The legend lists the categories top layer first, the reverse of add.txt
            cat = lambda k: boxes.nth(CATEGORIES - 1 - k)
            check(boxes.count() == CATEGORIES, f"{boxes.count()} category checkboxes (want {CATEGORIES})")
            check(not master.is_checked(), "layer starts hidden")

            info = page.evaluate("""() => {
              const layers = map.getStyle().layers;
              const src = l => (map.getSource(l.source)?.url ?? '');
              const agri = layers.map((l, i) => [i, l]).filter(([, l]) => src(l).includes('/worldcereal/'));
              const pop = layers.findIndex(l => src(l).includes('population_1km'));
              return { ids: agri.map(([, l]) => l.id), idx: agri.map(([i]) => i), pop,
                       vis: agri.map(([, l]) => map.getLayoutProperty(l.id, 'visibility')) };
            }""")
            print("  layers:", info, flush=True)
            check(len(info["ids"]) == CATEGORIES, "one map layer per category")
            check(info["pop"] >= 0 and all(i < info["pop"] for i in info["idx"]), "drawn below Population")
            check(all(v == "none" for v in info["vis"]), "all category layers hidden at start")

            def settle(tag):
                # Until the visible tiles are in (at most 30 s; 'idle' may never come
                # with software rendering)
                page.evaluate("""() => new Promise(r => {
                  const t0 = Date.now();
                  const poll = () => (map.areTilesLoaded() && map.loaded()) || Date.now() - t0 > 30000
                    ? r() : setTimeout(poll, 250);
                  map.triggerRepaint(); setTimeout(poll, 500);
                })""")
                page.wait_for_timeout(1500)
                png = page.locator("#map").screenshot(path=str(OUT / f"verify_{tag}.png"))
                return page.evaluate(COUNT_GREEN, base64.b64encode(png).decode())

            # US corn belt: dense annual crops (maize, soy)
            page.evaluate("() => map.jumpTo({ center: [-92.5, 41.8], zoom: 5.5 })")
            off = settle("off")
            master.check()
            on = settle("on")
            print(f"  green share of the map: off {off:.3f}, on {on:.3f}", flush=True)
            check(on > off + 0.10, "turning the layer on draws dark green cropland")
            vis = page.evaluate(f"() => {info['ids']}.map(id => map.getLayoutProperty(id, 'visibility'))")
            check(vis[0] == "visible" and all(v == "none" for v in vis[1:]),
                  f"only the first category shows at first ({vis})")
            cat(4).check()  # maize
            settle("maize")
            vis = page.evaluate(f"() => {info['ids']}.map(id => map.getLayoutProperty(id, 'visibility'))")
            check(vis[4] == "visible", "the maize checkbox shows maize")
            cat(0).uncheck()
            settle("maize_only")
            vis = page.evaluate(f"() => {info['ids']}.map(id => map.getLayoutProperty(id, 'visibility'))")
            check(vis[0] == "none" and vis[4] == "visible", "unticking temporary crops hides only that category")
            master.uncheck()
            vis = page.evaluate(f"() => {info['ids']}.map(id => map.getLayoutProperty(id, 'visibility'))")
            check(all(v == "none" for v in vis), "the layer toggle hides every category")
            master.check()
            vis = page.evaluate(f"() => {info['ids']}.map(id => map.getLayoutProperty(id, 'visibility'))")
            check(vis[0] == "none" and vis[4] == "visible", "turning it back on restores the ticked categories")
            # Whole-world view for a look at the global coverage
            cat(0).check()
            page.evaluate("() => map.jumpTo({ center: [10, 25], zoom: 1.6 })")
            settle("world")
            browser.close()
        server.shutdown()
    finally:
        shutil.rmtree(site, ignore_errors=True)

    relevant = [e for e in errors if "worldcereal" in e.lower() or "pageerror" in e
                or "extra source" in e.lower()]
    for e in errors:
        print("  console:", e[:300])
    check(not relevant, f"{len(relevant)} errors about the layer")
    if failures:
        print(f"{len(failures)} checks failed")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
