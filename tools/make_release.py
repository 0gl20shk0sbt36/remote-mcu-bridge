#!/usr/bin/env python3
"""Build a deterministic release archive for this project.

    python3 tools/make_release.py            # version from the git tag, else 1.0.0
    python3 tools/make_release.py --version 1.1.0 --output ~/share

Layout (GitHub source-archive style, one top-level directory):

    remote-mcu-bridge-<version>/
        <tracked project files>
        MANIFEST.sha256          # path + size + sha256 of every file above
    remote-mcu-bridge-<version>.zip
    remote-mcu-bridge-<version>.zip.sha256

Only tracked files are packaged, so build output, caches and private artifacts
cannot leak in. Entry timestamps and ordering are fixed so the archive hash is
reproducible for an unchanged tree.
"""

import argparse
import hashlib
import io
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXED_DATE = (2026, 11, 8, 0, 0, 0)


# Never ship internal development records, even if someone force-added one.
EXCLUDED_PREFIXES = (".internal/", "docs/validation/", "out/", "dist/")


def tracked_files():
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode()
    files = [name for name in raw.split("\0") if name]
    if not files:
        raise SystemExit("no tracked files; run from a git checkout")
    leaked = [name for name in files if name.startswith(EXCLUDED_PREFIXES)]
    if leaked:
        raise SystemExit("refusing to package internal/derived files: " + ", ".join(sorted(leaked)))
    return sorted(files)


def tag_version():
    try:
        tag = (
            subprocess.check_output(
                ["git", "describe", "--tags", "--abbrev=0"], cwd=ROOT, stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
    except subprocess.CalledProcessError:
        return "1.0.0"
    return tag[1:] if tag.startswith("v") else tag


def manifest(files):
    rows = ["# sha256  size  path"]
    for name in files:
        data = (ROOT / name).read_bytes()
        rows.append(f"{hashlib.sha256(data).hexdigest()}  {len(data):>8}  {name}")
    return "\n".join(rows) + "\n"


def write_entry(archive, name, data):
    info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=None, help="release version; defaults to the git tag")
    parser.add_argument("--output", type=Path, default=None, help="output directory")
    args = parser.parse_args(argv)

    version = args.version or tag_version()
    stem = f"remote-mcu-bridge-{version}"
    out = (args.output or (ROOT / "dist")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    archive_path = out / f"{stem}.zip"

    files = tracked_files()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in files:
            write_entry(archive, f"{stem}/{name}", (ROOT / name).read_bytes())
        write_entry(archive, f"{stem}/MANIFEST.sha256", manifest(files).encode())
    data = buffer.getvalue()
    archive_path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    (out / f"{stem}.zip.sha256").write_text(f"{digest}  {stem}.zip\n")

    print(f"{archive_path}")
    print(f"files:   {len(files)}")
    print(f"bytes:   {len(data)}")
    print(f"sha256:  {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
