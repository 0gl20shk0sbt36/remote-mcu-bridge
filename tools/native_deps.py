#!/usr/bin/env python3
"""Acquire only the pinned tiny cJSON dependency for native protocol fixtures."""

import argparse
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PIN = "b2890c8d76bbb64e710585ebc0a917196b9c67e7"


def cjson():
    candidates = [Path(os.environ.get("RMB_CJSON_PATH", ROOT / ".deps/cJSON"))]
    if os.environ.get("IDF_PATH"):
        candidates.insert(0, Path(os.environ["IDF_PATH"]) / "components/json/cJSON")
    for path in candidates:
        if (path / "cJSON.c").is_file() and (path / "cJSON.h").is_file():
            return path.resolve()
    raise RuntimeError(
        "cJSON missing: run python3 tools/native_deps.py, or set RMB_CJSON_PATH / IDF_PATH"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache", type=Path, help="local cJSON Git object mirror (no SDK required)"
    )
    args = parser.parse_args()
    dest = Path(os.environ.get("RMB_CJSON_PATH", ROOT / ".deps/cJSON")).resolve()
    if not dest.exists():
        subprocess.run(["git", "init", "-q", str(dest)], check=True)
    source = str(args.cache.resolve()) if args.cache else "https://github.com/DaveGamble/cJSON.git"
    subprocess.run(["git", "-C", str(dest), "fetch", "--depth=1", source, PIN], check=True)
    subprocess.run(["git", "-C", str(dest), "checkout", "-q", "--detach", "FETCH_HEAD"], check=True)
    head = subprocess.check_output(["git", "-C", str(dest), "rev-parse", "HEAD"], text=True).strip()
    if head != PIN:
        raise RuntimeError("cJSON commit mismatch")
    if subprocess.check_output(
        ["git", "-C", str(dest), "status", "--porcelain", "--untracked-files=no"], text=True
    ).strip():
        raise RuntimeError("modified cJSON checkout")
    print("cJSON pinned:", head)


if __name__ == "__main__":
    main()
