#!/usr/bin/env bash
# Unattended run of the WorldCereal agriculture layers, from download to verified PMTiles.
#
#   1. scripts/build_worldcereal_30s.py counts / mosaic / tiles (each resumes)
#   2. scripts/verify_agriculture_layer.py: builds the demo pages and checks the
#      agriculture layer in headless Chromium
#
# No git operations: the PMTiles are left in data/persistent/worldcereal/ to commit by hand.
#
# Safe to start any number of times: a lock keeps one run at a time and finished stages
# leave markers in data/temp/worldcereal/. Cron restarts it until the DONE marker exists,
# then the cron entry removes itself:
#   */15 * * * * /root/MoM-map/scripts/worldcereal_pipeline.sh >> /root/MoM-map/data/temp/worldcereal/pipeline.log 2>&1
#   @reboot      (same)
#
# Runs in the mom-layers env (environment-layers.yml), set up on first start if missing.
#
# Usage: scripts/worldcereal_pipeline.sh
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$REPO/data/temp/worldcereal"
# The mom-layers env (environment-layers.yml): GDAL, pmtiles, Playwright with its own Chromium
PY="${WORLDCEREAL_PY:-/root/miniconda3/envs/mom-layers/bin/python}"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/root/.cache/ms-playwright-mom}"
# What `conda activate` would set: GDAL cannot find PROJ's database without it
ENV_PREFIX="$(dirname "$(dirname "$PY")")"
export PROJ_DATA="$ENV_PREFIX/share/proj" GDAL_DATA="$ENV_PREFIX/share/gdal"
CRON_TAG="worldcereal_pipeline.sh"
mkdir -p "$STATE"
cd "$REPO" || exit 1

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [pipeline] $*"; }

exec 9>"$STATE/pipeline.lock"
flock -n 9 || exit 0  # already running

remove_cron() {
  crontab -l 2>/dev/null | grep -v "$CRON_TAG" | crontab -
}

if [ -e "$STATE/DONE" ]; then
  remove_cron
  exit 0
fi
if [ -e "$STATE/NEEDS_ATTENTION" ]; then
  log "stopped: $(cat "$STATE/NEEDS_ATTENTION") (delete $STATE/NEEDS_ATTENTION to retry)"
  exit 1
fi

stage() {  # stage NAME COMMAND...: runs once, marks success
  local name="$1"; shift
  [ -e "$STATE/stage_$name.done" ] && return 0
  log "stage $name: start"
  if "$@"; then
    touch "$STATE/stage_$name.done"
    log "stage $name: done"
  else
    log "stage $name: FAILED (exit $?), retried on the next start"
    exit 1
  fi
}

if ! "$PY" -c "import osgeo, pmtiles, playwright" 2>/dev/null; then
  log "setting up the mom-layers env"
  scripts/setup_layers_env.sh || { log "env setup FAILED, retried on the next start"; exit 1; }
fi

stage counts "$PY" -u scripts/build_worldcereal_30s.py counts 1
stage mosaic "$PY" -u scripts/build_worldcereal_30s.py mosaic
stage tiles  "$PY" -u scripts/build_worldcereal_30s.py tiles
stage verify "$PY" -u scripts/verify_agriculture_layer.py

# The 10 m downloads are kept until the tiles are built and verified
rm -f "$STATE/downloads/"*.tif
touch "$STATE/DONE"
log "verified, downloads deleted; done (nothing committed: commit the layer by hand)"
remove_cron
exit 0
