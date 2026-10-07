#!/usr/bin/env bash
# Unattended run of the WorldCereal agriculture layers, from download to push.
#
#   1. scripts/build_worldcereal_30s.py counts / mosaic / tiles (each resumes)
#   2. scripts/verify_agriculture_layer.py: builds the demo pages and checks the
#      agriculture layer in headless Chromium
#   3. one commit of the scripts, map code and PMTiles, pushed to main
#
# Safe to start any number of times: a lock keeps one run at a time and finished stages
# leave markers in data/temp/worldcereal/. Cron restarts it until the DONE marker exists,
# then the cron entry removes itself:
#   */15 * * * * /root/MoM-map/scripts/worldcereal_pipeline.sh >> /root/MoM-map/data/temp/worldcereal/pipeline.log 2>&1
#   @reboot      (same)
#
# Runs in the mom-layers env (environment-layers.yml), set up on first start if missing.
#
# Usage: scripts/worldcereal_pipeline.sh [--no-push]
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

stage counts "$PY" -u scripts/build_worldcereal_30s.py counts 3
stage mosaic "$PY" -u scripts/build_worldcereal_30s.py mosaic
stage tiles  "$PY" -u scripts/build_worldcereal_30s.py tiles
stage verify "$PY" -u scripts/verify_agriculture_layer.py

if [ "${1:-}" = "--no-push" ]; then
  log "verified; --no-push given, not committing"
  exit 0
fi

# One commit with everything the layer needs, then push to main
# (the local-only check scripts, verify_agriculture_layer.py and serve_demo.py, stay out)
FILES=(environment-layers.yml scripts/setup_layers_env.sh scripts/remote_zip.py scripts/build_worldcereal_30s.py
       scripts/worldcereal_pipeline.sh scripts/build_value_pmtiles.py js/extra-sources.js css/styles.css
       sub_pages/demo_202610/add.txt data/persistent/worldcereal)
if [ ! -e "$STATE/stage_commit.done" ]; then
  branch="$(git rev-parse --abbrev-ref HEAD)"
  if [ "$branch" != "main" ]; then
    echo "checkout is on '$branch', not main" > "$STATE/NEEDS_ATTENTION"; log "$(cat "$STATE/NEEDS_ATTENTION")"; exit 1
  fi
  git add -- "${FILES[@]}"
  if git diff --cached --quiet; then
    log "nothing to commit"
  else
    git commit -m "Add WorldCereal agriculture layer to demo_202610

Per-category share of each 30\" cell (temporary crops, maize, winter and spring
cereals, irrigated) from ESA WorldCereal 2021 v100 (CC BY 4.0), as value PMTiles,
hidden by default below Population, with a toggle per category.
Rebuild: scripts/worldcereal_pipeline.sh --no-push" -- "${FILES[@]}" || { log "commit failed"; exit 1; }
  fi
  touch "$STATE/stage_commit.done"
fi

for attempt in 1 2 3; do
  if git push origin main; then
    touch "$STATE/DONE"
    log "pushed $(git rev-parse --short HEAD) to main; done"
    remove_cron
    exit 0
  fi
  # main moved on: replay the commit on top (no-op if it does not apply cleanly)
  git fetch origin main
  if ! git rebase origin/main; then
    git rebase --abort
    echo "rebase onto origin/main conflicts; commit $(git rev-parse --short HEAD) is local only, push it by hand" \
      > "$STATE/NEEDS_ATTENTION"
    log "$(cat "$STATE/NEEDS_ATTENTION")"
    exit 1
  fi
done
log "push failed 3 times, retried on the next start"
exit 1
