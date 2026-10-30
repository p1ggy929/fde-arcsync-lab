#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
ARCSYNC_PYTHON="${ARCSYNC_PYTHON:-python3}"
if [ -x .venv/bin/python ] && [ "$ARCSYNC_PYTHON" = python3 ]; then ARCSYNC_PYTHON="$PWD/.venv/bin/python"; fi
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
# Automated tests always use disposable SQLite, never an operator's MySQL.
export ARCSYNC_DATABASE_URL=''
node --check src/frontend/frontend-sync.js
node --check src/frontend/app.js
node --check src/frontend/manage.js
node --check src/frontend/chat.js
"$ARCSYNC_PYTHON" -m unittest discover -s tests -v
