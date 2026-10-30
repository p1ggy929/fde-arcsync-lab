#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
ARCSYNC_PYTHON="${ARCSYNC_PYTHON:-python3}"
if [ -x .venv/bin/python ] && [ "$ARCSYNC_PYTHON" = python3 ]; then ARCSYNC_PYTHON="$PWD/.venv/bin/python"; fi
node --check src/frontend/app.js
"$ARCSYNC_PYTHON" -m compileall -q src/arcsync
mkdir -p dist
"$ARCSYNC_PYTHON" scripts/package_app.py
