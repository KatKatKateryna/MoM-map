# Data sources of the map layers

Where the data of the extra map layers comes from, how it is processed, and how to rebuild
each layer from scratch. The layers are wired into the demo pages in
`sub_pages/demo_<yyyymm>/add.txt` (`EXTRA_SOURCES`); the current one is `demo_202610`.

| Layer on the map | PMTiles | Source | Scripts |
| --- | --- | --- | --- |
| Observed floods, 2014-2024 (AI4G) | `data/persistent/ai4g/ai4g_flood_1km.pmtiles` | Microsoft AI for Good flood dataset (Sentinel-1) | `scripts/ai4g/` |
| Flood extent, any model | `data/persistent/flood_max/flood_extent_1km.pmtiles` | WRI Aqueduct Floods + Copernicus GloFAS | `scripts/aqueduct/`, `scripts/glofas/`, `scripts/flood_extent/` |
| Population | `data/persistent/population_1km/population_1km.pmtiles` | JRC GHS-POP R2023A, 2025, 1 km | `scripts/population/` |
| Agriculture (WorldCereal 2021) | `data/persistent/worldcereal/worldcereal_<category>_30s.pmtiles` | ESA WorldCereal 2021 v100 | `scripts/worldcereal/` |
| River floodplains | `data/persistent/river_plains/river_plains_30s.pmtiles` | GFPLAIN250m | `scripts/river_plains/` |

Also built, but commented out or not on the page: the single flood depth layers
(`aqueduct_riverine_rp1000`, `aqueduct_coastal_rp1000`, `glofas_rp500_1km`), the deepest
of them (`flood_max_1km`) and the AI4G 5" south-east flooded share.

## Common processing

All layers except the floodplains are **value PMTiles**: the tiles hold values, not colours,
and the map colours and filters them in the browser (`js/extra-sources.js`, `values`
entries: colour ramp, legend, range slider).

- **Grid.** Each dataset is first brought onto the global 30" grid (1/120 degree, ~1 km,
  -180..180 x -90..90, EPSG:4326, 43200 x 21600; the grid of the Aqueduct rasters) as
  Float32 with nodata -9999. Population is the exception: it goes straight from its own
  1 km Mollweide grid to Web Mercator.
- **Web Mercator.** The 30" raster is warped to EPSG:3857 at the pixel size of zoom 8
  (`scripts/common/layer_pmtiles.py`), averaging the valid cells (or taking their maximum,
  for classes).
- **Tiles** (`scripts/common/build_value_pmtiles.py`): zoom 0 to 7, 256 px. Each zoom level is
  warped from the one above, averaging the valid cells under each pixel (or the maximum).
  Each pixel stores the index of its value on a log scale of 2 % steps,
  `index = round(log(value) / log(1.02)) + 2000` (0 = no data or zero), as a lossless RGB
  WebP in the Terrarium encoding (`index = R*256 + G + B/256 - 32768`). The archive metadata
  `value_scale` (`{"type": "log", "ratio": 1.02, "offset": 2000}`) tells the map how to turn
  indexes back into values.
- **Intermediate rasters** go to `data/temp/<dataset>/` (gitignored) and are kept: the 30"
  rasters (`*_4326.tif`) and the Web Mercator ones (`*_3857.tif`). Only the per-zoom tiling
  rasters are removed. The PMTiles go to `data/persistent/<folder>/` and are committed.

## Observed floods, 2014-2024 (AI4G)

**Source.** The AI4G flood dataset, Microsoft AI for Good Lab (Nature Communications 2025):
flooding detected in all Sentinel-1 radar images of 2014-2024. Hugging Face dataset
`ai-for-good-lab/ai4g-flood-dataset` (https://huggingface.co/datasets/ai-for-good-lab/ai4g-flood-dataset),
one GeoTIFF per 3 x 3 degree tile. The `-240m-buffer.tif` version is used: 0.6" pixels,
0 = no flood, 1 = exclusion mask (rough terrain, arid or urban, not assessed),
2 = flooded at least once, with a 240 m buffer around the detections.

**Processing.**
1. `scripts/ai4g/1_download_reduce_30s.py`: lists the tiles through the Hugging Face API,
   downloads each into memory (never to disk) and reduces it to 30" by the **maximum** of
   each 50 x 50 block: 2 if any pixel was flooded, else 1 if any is masked, else 0. Tiles
   start on whole degrees, so the blocks nest exactly. Some files are empty (0 bytes) on
   the server (a known LFS problem, dataset discussion #3); they count as "no tile".
   Reduced tiles: `data/temp/ai4g/30s/`. `--mosaic` writes them into one global 30"
   GeoTIFF; the one committed is `data/persistent/ai4g/ai4g_30s.tif` (Byte, 255 = no tile).
2. `scripts/ai4g/2_build_pmtiles.py`: keeps classes 1 and 2 (0 and no tile become nodata)
   and tiles them with the **maximum** at every zoom, so classes never blend: a pixel with
   any flooding under it stays 2.

**On the map.** Only class 2 is drawn (orange, "Flooded at least once"); class 1 is
transparent and left out of the legend. No slider.

**Rebuild.**
```bash
$PY scripts/ai4g/1_download_reduce_30s.py 6            # 6 workers; re-run to resume
$PY scripts/ai4g/2_build_pmtiles.py                    # from data/temp/ai4g/30s/
# or, without downloading, from the committed mosaic:
$PY scripts/ai4g/2_build_pmtiles.py data/persistent/ai4g/ai4g_30s.tif
```

**AI4G 5" south-east (not on the page).** `scripts/ai4g/se_5s_1_download_reduce.py` reduces
the tiles around Mississippi, Florida, North Carolina and West Virginia (+100 km) to 5"
as the flooded share in % (101 = masked), and `scripts/ai4g/se_5s_2_build_pmtiles.py` tiles
it up to zoom 10 with a linear encoding.

## Flood extent, any model

Every ~1 km cell flooded in at least one of three flood hazard models:

### WRI Aqueduct Floods (riverine and coastal, 1-in-1000 years)

**Source.** WRI Aqueduct Floods v2 (https://aqueduct.wridata.org/AqueductFloods20/index.html):
global inundation depth in metres, 30" (~1 km) GeoTIFFs. Two of them are used, linked
from that page (checked 2026-10-13):
- riverine: https://aqueduct.wridata.org/AqueductFloods20/inunriver_historical_000000000WATCH_1980_rp01000.tif
  (historical climate, WATCH 1980, 1-in-1000 years; 95.5 MB)
- coastal: https://aqueduct.wridata.org/AqueductFloods20/inuncoast_historical_wtsub_hist_rp1000_0.tif
  (historical sea level, with subsidence, 1-in-1000 years; 47.1 MB)

WRI corrected a bias-correction error in the riverine maps on 2020-10-20; the files on
that page are the corrected ones.

**Processing.** `scripts/aqueduct/1_download.py` downloads them to `data/temp/aqueduct/`.
`scripts/aqueduct/2_build_pmtiles.py` turns dry (0) and negative cells into nodata, so
lower zooms average flooded cells only, and keeps the result as
`data/temp/aqueduct/<name>_4326.tif` (used by the flood extent) plus
`data/persistent/aqueduct/<name>.pmtiles`.

### Copernicus GloFAS (river, 1-in-500 years)

**Source.** The CEMS-GloFAS flood hazard maps (JRC):
https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/CEMS-GLOFAS/flood_hazard/. Tiles of river
flood depth in metres at 3" (~90 m), from the `RP500` folder, plus the
`Permanent_WaterBodies` and `Spurious_Depths` masks of the same tiles.

**Processing.**
1. `scripts/glofas/1_download.py` downloads the three folders to `data/temp/glofas/`.
2. `scripts/glofas/2_clean_mosaic.py RP500 keep`: removes permanent water (rivers, lakes,
   which JRC patches into the depth maps), so only flooding is left. Cells in JRC's
   "spurious depth" areas (likely DEM artefacts in small channels) are **kept** (mode
   `keep`; `flag`, `mask` and `deep` are alternatives). Output: Int16 depth in cm
   per tile, `data/temp/glofas/RP500_keep/`, mosaicked by `RP500_keep.vrt`.
3. `scripts/glofas/3_build_pmtiles.py`: averages the 90 m depths onto the 30" grid,
   counting only flooded cells (a 1 km cell holds the mean depth of its flooded 90 m
   cells), in metres: `data/temp/glofas/glofas_rp500_1km_4326.tif` and
   `data/persistent/glofas/glofas_rp500_1km.pmtiles`.

`scripts/glofas/permanent_water.py` (optional, not used by the layers) merges the
permanent water tiles into one global GeoTIFF.

### Combined

`scripts/flood_extent/build_pmtiles.py` takes, per 30" cell, the deepest of the three
depth rasters:
- `combined`: that depth, `data/persistent/flood_max/flood_max_1km.pmtiles` (not on the page)
- `extent`: **1 wherever that depth is above 0**, nodata elsewhere,
  `data/persistent/flood_max/flood_extent_1km.pmtiles`

**On the map.** One colour, no slider.

**Rebuild.**
```bash
$PY scripts/aqueduct/1_download.py
$PY scripts/aqueduct/2_build_pmtiles.py
$PY scripts/glofas/1_download.py                       # re-run to resume
$PY scripts/glofas/2_clean_mosaic.py RP500 keep 6
$PY scripts/glofas/3_build_pmtiles.py
$PY scripts/flood_extent/build_pmtiles.py              # combined and extent
```

## Population

**Source.** JRC Global Human Settlement Layer, GHS-POP R2023A, epoch 2025, World
Mollweide (ESRI:54009): residential population per cell.
- 1000 m (the map's layer):
  https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/GHS_POP_E2025_GLOBE_R2023A_54009_1000/V1-0/GHS_POP_E2025_GLOBE_R2023A_54009_1000_V1_0.zip
- 100 m, the alternative when finer detail is needed:
  https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/GHS_POP_E2025_GLOBE_R2023A_54009_100/V1-0/GHS_POP_E2025_GLOBE_R2023A_54009_100_V1_0.zip

Contents of each zip (checked 2026-10-13 from the zip listings, nothing downloaded):

| | 1 km zip | 100 m zip |
| --- | --- | --- |
| Zip size | 323 MB | 5.25 GB |
| GeoTIFF, unpacked | 262 MB | 6.5 GB |
| `.tif.ovr` overviews (not used) | 88 MB | 2.2 GB |
| Also | `GHSL_Data_Package_2023.pdf`, `GHS_POP_GLOBE_R2023A_input_metadata.xlsx` | same |

The 1 km GeoTIFF: 36082 x 18000 cells of 1000 m, World Mollweide, Float64, nodata -200,
256 x 256 tiles. The 100 m header was not checked.

**Processing.**
1. `scripts/population/1_download.py` downloads the zip to `data/temp/population/`.
2. `scripts/population/2_build_pmtiles.py` extracts the GeoTIFF from the downloaded zip
   into `data/temp/population/` (once), deletes the zip (step 1 then skips the download
   while the GeoTIFF is there) and warps it to Web Mercator (zoom-8 pixels), averaging. Mollweide cells are equal-area
   (1 km²), so people per cell is people per km², and averaging keeps it a density: lower
   zooms show the mean people per km² under each pixel. Cells with 0 people are not drawn.
   Output: `data/persistent/population_1km/population_1km.pmtiles`.

**100 m alternative.** `1_download.py --100m` and `2_build_pmtiles.py --100m` use the
100 m file instead. Its values (people per 100 m cell, 0.01 km²) are multiplied by 100 to
people per km², so it fits the same colour ramp and slider as the 1 km layer. It is tiled
up to zoom 10 (~150 m pixels at the equator; `--maxzoom` to change), and the whole world
at that zoom is far too large for this host, so give a box with `--bbox WEST,SOUTH,EAST,NORTH`.
Output: `data/persistent/population_100m/population_100m.pmtiles` (not on the page). On the
page it needs its own `EXTRA_SOURCES` entry (the map reads the zoom range from the
archive itself).

**On the map.** Red ramp from 0.1 to 100,000 people; the slider uses a power scale
(value ~ position³) and opens hiding cells under 200 people.

**Rebuild.**
```bash
$PY scripts/population/1_download.py
$PY scripts/population/2_build_pmtiles.py
# 100 m alternative, for one area (here the south-east US):
$PY scripts/population/1_download.py --100m
$PY scripts/population/2_build_pmtiles.py --100m --bbox -92,24,-75,41
```

**Note.** The committed `population_1km.pmtiles` (commit `4f7e48d`, 2026-10-07) was built
before these scripts existed, so the exact steps used then are not recorded. A rebuild
with the scripts may differ slightly from it, for example at the edges of the extent.

## Agriculture (WorldCereal 2021)

**Source.** ESA WorldCereal 2021 v100, 10 m, (c) ESA WorldCereal Consortium, CC BY 4.0,
Zenodo record 7875105 (https://zenodo.org/records/7875105). One zip per product, each holding
one GeoTIFF per agro-ecological zone (AEZ), 0.3" in EPSG:4326. Values: 100 = the class,
0 = not the class, 254 = not cropland (crop type and irrigation products), 255 = no data.

**Processing.** `scripts/worldcereal/build_30s.py`, in three resumable stages:
1. **counts**: the zips (7-20 GB each) are never downloaded whole; each AEZ GeoTIFF is
   fetched on its own with one HTTP range request (`scripts/worldcereal/remote_zip.py`).
   At full resolution, it counts the class pixels (100) and the valid pixels (not 255) in
   each 30" cell; each pixel goes to the cell holding its centre. The files' overviews
   are not used: they are mode resampled and undercount small fields (-1 % at 2x, -9 % at
   8x). Counts: `data/temp/worldcereal/counts/<product>/<aez>.npz`.
2. **mosaic**: the share (%) of each cell's valid area in the class, summing the counts of
   overlapping AEZs. Cells need at least 100 valid 10 m pixels (1 % of the cell). Products
   of several seasons are combined by the larger share per cell. Cells with valid data but
   no class hold 0, so lower zooms average the share over all land.
   `data/temp/worldcereal/<category>_30s.tif`.
3. **tiles**: value PMTiles up to zoom 7,
   `data/persistent/worldcereal/worldcereal_<category>_30s.pmtiles`.

Six categories, by the dataset's product names:

| Category | Products |
| --- | --- |
| temporarycrops | `tc-annual_temporarycrops` |
| activecropland | `tc-wintercereals_activecropland` + `tc-maize-main_activecropland` + `tc-maize-second_activecropland` |
| wintercereals | `tc-wintercereals_wintercereals` |
| springcereals | `tc-springcereals_springcereals` |
| maize | `tc-maize-main_maize` + `tc-maize-second_maize` |
| irrigation | `tc-wintercereals_irrigation` + `tc-maize-main_irrigation` + `tc-maize-second_irrigation` |

Orchards, plantations and pasture are not part of WorldCereal 2021.

`scripts/worldcereal/pipeline.sh` runs the three stages and then
`scripts/worldcereal/verify_agriculture_layer.py`, which builds the demo pages and checks the
layer in headless Chromium. It is unattended and safe to restart, from cron. A full counts
run downloads ~156 GB, more than this host's disk, so the pipeline deletes the 10 m files
once the tiles are verified. It never commits anything.

**The demo uses the global layers.** `demo_202610` loads the global
`worldcereal_<category>_30s.pmtiles`, built by `pipeline.sh` (all stages done and verified
2026-10-14). The samples `worldcereal_<category>_30s_sample.pmtiles` are still in the
folder but not on the page. They were built with `--window` (all in
`data/temp/worldcereal_sample/`) from AEZ 46173 only, the US Corn Belt (98°W-90°W,
38°N-42.5°N). Only temporary crops, maize and winter cereals have data in them; active
cropland, irrigation and spring cereals are empty archives.

**On the map.** One dark green ramp per category, one checkbox each. The slider (share
of the cell, 1-100 %) opens hiding cells under 20 %.

**Rebuild.**
```bash
scripts/worldcereal/pipeline.sh                                   # full run, all stages + check
# or by hand:
$PY scripts/worldcereal/build_30s.py counts 3 mosaic tiles
# the sample (the window is an example around AEZ 46173):
$PY scripts/worldcereal/build_30s.py --aez 46173 --window -98,38,-90,42.5
```

## River floodplains

**Source.** GFPLAIN250m global floodplains (Nardi et al. 2006, 2018), mapped with a
geomorphic approach from the NASA SRTM v4.1 elevation model, 250 m (8.33"), one GeoTIFF
per continent (AF, AS, EU, NA, OC, SA), 0 = floodplain, 255 = nodata. The download is not
scripted: the files go by hand to `data/temp/river_plains/<continent>/*.TIF`.

**Processing.** `scripts/river_plains/build_30s.py` mosaics the six continents onto the 30"
grid with the maximum (floodplain if any source cell overlapping the cell is),
`data/temp/river_plains_30s.tif`, and tiles it as a raster PMTiles of one blue colour
(`scripts/river_plains/build_river_plains.py`) with the maximum at every zoom.

## How to rebuild

1. Set up the `mom-layers` conda env (GDAL, pmtiles, Playwright) once:
   `scripts/setup_layers_env.sh` (from `environment-layers.yml`; installs from conda-forge
   and PyPI).
2. Use its Python, with GDAL's data paths:
   ```bash
   PY=/root/miniconda3/envs/mom-layers/bin/python
   export PROJ_DATA=/root/miniconda3/envs/mom-layers/share/proj GDAL_DATA=/root/miniconda3/envs/mom-layers/share/gdal
   ```
3. Run the steps of each dataset in order (the **Rebuild** blocks above). Numbered scripts
   are steps of one dataset, from download to PMTiles. Downloads resume when re-run.
   The flood extent needs the Aqueduct and GloFAS steps first.
4. Check the page locally: `$PY scripts/serve_demo.py 8080 --tiles-from https://katkatkateryna.github.io/MoM-map/data/tiles/`,
   then open http://127.0.0.1:8080/demo_202610/.
5. Commit the new PMTiles in `data/persistent/` by hand.

Layout of `scripts/`:
```
common/         build_value_pmtiles.py (value tiles), layer_pmtiles.py (30" grid, Web
                Mercator, build_layer), download.py (resumable download)
aqueduct/       1_download.py, 2_build_pmtiles.py
glofas/         1_download.py, 2_clean_mosaic.py, 3_build_pmtiles.py, permanent_water.py
flood_extent/   build_pmtiles.py (needs aqueduct/ and glofas/)
ai4g/           1_download_reduce_30s.py, 2_build_pmtiles.py, se_5s_1_download_reduce.py, se_5s_2_build_pmtiles.py
population/     1_download.py, 2_build_pmtiles.py
worldcereal/    build_30s.py, remote_zip.py, pipeline.sh, verify_agriculture_layer.py
river_plains/   build_river_plains.py, build_30s.py
serve_demo.py, setup_layers_env.sh
```
