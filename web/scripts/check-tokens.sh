#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mapfile -d '' -t sources < <(find src -type f \( -name '*.ts' -o -name '*.tsx' -o -name '*.js' -o -name '*.jsx' -o -name '*.css' \) ! -path src/styles.css -print0)
status=0
check() {
  local result=0
  grep -HnP "$1" "${sources[@]}" || result=$?
  if (( result == 0 )); then
    echo "error: $2" >&2
    status=1
  elif (( result != 1 )); then
    exit "$result"
  fi
}
check '\b\d+(?:\.\d+)?px\b' "Use a size token from src/styles.css instead of a pixel value"
check '\b(?:text-(?:xs|sm|base|lg|xl|[2-9]xl))(?![-\w])' "Use a type token from src/styles.css"
check '(?:\b[-a-z0-9]+-\[[^\s\]]+\]|(?<![\w-])\[[-a-z]+:[^\s\]]+\])(?=$|[\s/\x22\x27`])' "Define arbitrary Tailwind values in src/styles.css"
exit $status
