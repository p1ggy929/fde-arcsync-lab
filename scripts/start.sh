#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
if [ -n "${ARCSYNC_PYTHON:-}" ]; then
    launcher_python="$ARCSYNC_PYTHON"
else
    launcher_python=''
    for candidate in "$PWD/.venv/bin/python" python3 python3.14 python3.13 python3.12 python3.11 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
            launcher_python="$candidate"
            break
        fi
    done
fi
if [ -z "$launcher_python" ]; then
    echo '未找到 Python 3.11 或更高版本，请先安装：https://www.python.org/downloads/' >&2
    exit 1
fi
exec "$launcher_python" scripts/launch.py "$@"
