#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m arcsync.main --port "${PORT:-8765}"
