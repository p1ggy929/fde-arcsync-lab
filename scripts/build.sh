#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
node --check src/frontend/app.js
python3 -m compileall -q src/arcsync
mkdir -p dist
python3 scripts/package_app.py
