#!/usr/bin/env bash
# Creates (or updates) the mom-layers conda env from environment-layers.yml and installs
# Playwright's Chromium into its own folder, apart from any other Playwright install on
# the host (default /root/.cache/ms-playwright-mom, or $PLAYWRIGHT_BROWSERS_PATH).
#
# Usage: scripts/setup_layers_env.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
CONDA="${CONDA_EXE:-/root/miniconda3/bin/conda}"
ENV_NAME=mom-layers
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/root/.cache/ms-playwright-mom}"

if "$CONDA" env list | grep -qE "^$ENV_NAME\s"; then
  "$CONDA" env update -n "$ENV_NAME" -f "$REPO/environment-layers.yml" --prune
else
  "$CONDA" env create -f "$REPO/environment-layers.yml"
fi
PY="$("$CONDA" run -n "$ENV_NAME" python -c 'import sys; print(sys.executable)')"
"$PY" -m playwright install chromium
"$PY" -c "from osgeo import gdal; import pmtiles, playwright, PIL, numpy; print('mom-layers ok, GDAL', gdal.__version__)"
echo "python: $PY"
echo "browsers: $PLAYWRIGHT_BROWSERS_PATH"
