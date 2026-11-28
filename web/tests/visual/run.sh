#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/../../.." && pwd)"
image="swb-visual:playwright-1.65.1"
snapshots="$root/web/tests/visual/__screenshots__"
artifacts="${SWB_VISUAL_ARTIFACTS:-$root/web/test-results/visual}"
mkdir -p "$snapshots" "$artifacts"
artifacts="$(cd "$artifacts" && pwd)"

if [[ "${SWB_VISUAL_REUSE_IMAGE:-0}" != 1 ]]; then
  docker build --platform linux/amd64 -t "$image" -f "$root/web/tests/visual/Dockerfile" "$root"
fi

entrypoint=()
if [[ "${1:-}" == --compact-baselines ]]; then
  entrypoint=(--entrypoint /app/.venv/bin/python --workdir /app/web/tests/visual)
  set -- compact_baselines.py
fi

docker run --rm --init --ipc=host --platform linux/amd64 --hostname swb-visual "${entrypoint[@]}" \
  --user "$(id -u):$(id -g)" -e HOME=/tmp -e SWB_VISUAL_STRICT_CLS -e "SWB_VISUAL_WORKERS=${SWB_VISUAL_WORKERS:-2}" \
  --mount "type=bind,source=$snapshots,target=/app/web/tests/visual/__screenshots__" \
  --mount "type=bind,source=$artifacts,target=/artifacts" \
  "$image" "$@"
