#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

cd "$root"
uv build --quiet --out-dir "$work/dist"
wheels=("$work"/dist/*.whl)
if [[ ${#wheels[@]} -ne 1 || ! -f "${wheels[0]}" ]]; then
    echo "Expected exactly one built wheel." >&2
    exit 1
fi
printf 'Built wheel: %s\n' "${wheels[0]##*/}"
venv="$work/venv"
uv venv --quiet "$venv"
uv pip install --quiet --python "$venv/bin/python" "${wheels[0]}[test]"

mkdir "$work/home" "$work/run"
cd "$work/run"
clean() {
    env -i HOME="$work/home" PATH="$venv/bin:/usr/bin:/bin" "$@"
}

clean "$venv/bin/python" -I -B -c 'from pathlib import Path; import sys, switchboard; assert Path(switchboard.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())'
clean "$venv/bin/swb" --help >/dev/null
echo "Installed swb CLI: ok"
clean "$venv/bin/python" -I -B "$root/scripts/smoke-wheel-web.py" "${wheels[0]}"
: > empty.yaml
status="$(clean SWITCHBOARD_CONFIG="$PWD/empty.yaml" SWITCHBOARD_DATA="$PWD/data" "$venv/bin/switchboard" blocks check)"
printf '%s\n' "$status"
if [[ "$status" != "mcp: ok" ]]; then
    echo "Expected only the default MCP block for an empty config." >&2
    exit 1
fi

clean "$venv/bin/python" -I -B -m pytest -q -p no:cacheprovider -o asyncio_default_fixture_loop_scope=function \
    --basetemp "$work/test-data" "$root/tests/test_fresh_install.py"
