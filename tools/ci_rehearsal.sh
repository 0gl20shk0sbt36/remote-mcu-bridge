#!/usr/bin/env bash
# Local rehearsal of .github/workflows/ci.yml — run this before pushing.
#
# The hosted runners are plain Ubuntu plus, for the ESP-IDF jobs, the
# espressif/idf container. Containers are not always available locally, so this
# script reproduces the two things that actually differ: a stripped environment
# (no inherited IDF variables, minimal PATH, no ~/tmp) and the exact step bodies.
#
#   ./tools/ci_rehearsal.sh              # everything (needs IDF_PATH + IDF_TOOLS_PATH)
#   ./tools/ci_rehearsal.sh native       # native job only
#   ./tools/ci_rehearsal.sh esp-idf      # ESP-IDF jobs only
#
# Exit code 0 means the workflow should pass; anything else is a real failure.
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
MODE=${1:-all}
FAILED=0
SCRATCH_BASE=${TMPDIR:-$HOME/tmp}
mkdir -p "$SCRATCH_BASE"
SCRATCH=$(mktemp -d "$SCRATCH_BASE/rmb-ci-rehearsal-XXXXXX")
trap 'rm -rf "$SCRATCH"' EXIT

say() { printf '\n=== %s ===\n' "$*"; }
fail() {
    printf 'FAILED: %s\n' "$*"
    FAILED=1
}

# A "hosted runner" HOME: no ~/tmp, nothing inherited from this machine.
CLEAN_HOME="$SCRATCH/home"
mkdir -p "$CLEAN_HOME"

clean_env() {
    env -u IDF_PATH -u IDF_TOOLS_PATH -u IDF_PYTHON_ENV_PATH -u TMPDIR \
        HOME="$CLEAN_HOME" PATH=/usr/local/bin:/usr/bin:/bin "$@"
}

native_job() {
    say "native (no SDK)"
    (
        cd "$ROOT" || exit 1
        clean_env python3 tools/native_deps.py || exit 1
        clean_env cmake --preset native || exit 1
        clean_env cmake --build --preset native || exit 1
        clean_env ctest --preset native --output-on-failure || exit 1
    ) || fail "native job"
}

idf_environment() {
    # Mirrors the "Set up ESP-IDF toolchain" step.
    if [ -f "$IDF_PATH/export.sh" ]; then
        # shellcheck disable=SC1091
        . "$IDF_PATH/export.sh" >/dev/null 2>&1 \
            || echo "note: export.sh reported problems; using explicit tool paths"
    fi
    local d
    for d in \
        "$IDF_TOOLS_PATH"/tools/cmake/*/bin \
        "$IDF_TOOLS_PATH"/tools/ninja/*/bin \
        "$IDF_TOOLS_PATH"/tools/ninja/*/ \
        "$IDF_TOOLS_PATH"/tools/riscv32-esp-elf/*/riscv32-esp-elf/bin \
        "$IDF_TOOLS_PATH"/python_env/idf5.5_py*_env/bin; do
        [ -d "$d" ] && PATH="$d:$PATH"
    done
    local pythons=("$IDF_TOOLS_PATH"/python_env/idf5.5_py*_env/bin/python)
    if [ "${#pythons[@]}" -eq 1 ]; then
        export IDF_PYTHON_ENV_PATH="${pythons[0]%/bin/python}"
    fi
    export PATH
}

idf_job() {
    local profile=$1
    say "esp-idf ($profile)"
    (
        cd "$ROOT" || exit 1
        idf_environment
        command -v cmake >/dev/null || exit 1
        command -v ninja >/dev/null || exit 1
        command -v python3 >/dev/null || exit 1
        # build.py rejects the placeholder network identity in the enabled
        # profile, exactly as CI does; these values are public and never flashed.
        local creds="$SCRATCH/ci-credentials"
        printf 'ci-compile-only-ssid\nci-compile-only-password\nci-compile-only-token-0000000000\n' >"$creds"
        python3 tools/rmb.py configure --backend esp-idf --board esp32c3-stm32f103 \
            --profile "$profile" --credentials-file "$creds" || exit 1
        python3 tools/rmb.py build --backend esp-idf --board esp32c3-stm32f103 \
            --profile "$profile" --credentials-file "$creds" || exit 1
        if [ "$profile" = compile-enabled ]; then
            local build=out/esp-idf/esp32c3-stm32f103/compile-enabled
            local -a elfs
            mapfile -t elfs < <(find "$build" -maxdepth 1 -name '*.elf' -type f)
            [ "${#elfs[@]}" -eq 1 ] || exit 1
            python3 tools/check_module_link.py --base-elf "${elfs[0]}" || exit 1
            # Three fixtures compile against mbedtls and need the SDK sources.
            cmake --preset native || exit 1
            cmake --build --preset native || exit 1
            ctest --preset native --output-on-failure || exit 1
        fi
    ) || fail "esp-idf job ($profile)"
}

case "$MODE" in
    native) native_job ;;
    esp-idf | idf)
        [[ -n ${IDF_PATH:-} && -n ${IDF_TOOLS_PATH:-} ]] || {
            echo "IDF_PATH and IDF_TOOLS_PATH must be set for the ESP-IDF jobs" >&2
            exit 2
        }
        idf_job safe
        idf_job compile-enabled
        ;;
    all)
        native_job
        if [[ -n ${IDF_PATH:-} && -n ${IDF_TOOLS_PATH:-} ]]; then
            idf_job safe
            idf_job compile-enabled
        else
            echo "note: IDF_PATH/IDF_TOOLS_PATH unset — ESP-IDF jobs not rehearsed"
        fi
        ;;
    *)
        echo "usage: $0 [native|esp-idf|all]" >&2
        exit 2
        ;;
esac

if [ "$FAILED" -ne 0 ]; then
    echo
    echo "Rehearsal failed — CI would fail; do not push."
    exit 1
fi
echo
echo "Rehearsal passed: the workflow should be green."
