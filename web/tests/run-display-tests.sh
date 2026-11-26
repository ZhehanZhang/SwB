#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
output="$(mktemp -d .display-tests.XXXXXX)"
trap 'rm -rf "$output"' EXIT
node_modules/.bin/tsc --ignoreConfig --module nodenext --target es2022 \
  --rootDir . --outDir "$output" --types node --strict --skipLibCheck tests/display.test.ts
node --test "$output/tests/display.test.js"
