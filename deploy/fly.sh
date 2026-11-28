#!/bin/sh
set +x
set -eu

launcher="$(dirname "$0")/fly_guard.py"
if command -v python3 >/dev/null && python3 -c 'import tomllib' 2>/dev/null; then
    exec python3 "$launcher" "$@"
fi
if command -v uv >/dev/null; then
    exec uv run --no-project --python '>=3.11' -- python3 "$launcher" "$@"
fi
printf '%s\n' 'Install Python 3.11 or later, or install uv, to run the Fly deploy guard.' >&2
exit 1
