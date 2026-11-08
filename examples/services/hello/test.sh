#!/bin/sh
# Offline UNIT ONLY; no target, sockets, flash or production credentials.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/../../.." && pwd)
OUT=${TMPDIR:-"$HOME/.hermes/cache/scratch"}/hello-native
mkdir -p "$OUT"
cd "$ROOT"
CJSON=$(python3 -c 'from tools.native_deps import cjson; print(cjson())')
for variant in 1 2; do
  gcc -std=c11 -g -fsanitize=address,undefined -DSERVICE_VARIANT="$variant" \
    -I"$ROOT/include" -I"$CJSON" \
    examples/services/hello/service.c examples/services/hello/test_service.c \
    "$CJSON/cJSON.c" -lm -o "$OUT/hello-$variant"
  "$OUT/hello-$variant"
done
python3 examples/services/hello/test_manifest.py
