#!/usr/bin/env python3
"""Format only project-owned C/Python sources; works without a .git directory.

Install pinned tooling in an isolated environment first:
    python3 -m venv .venv-style
    .venv-style/bin/python -m pip install -r tools/format-requirements.txt
    python3 tools/format_code.py --check
Omit --check to rewrite files. On Windows use .venv-style\\Scripts\\python.exe.
The source roots deliberately exclude SDKs, dependencies and build artifacts.
"""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (
    "backends",
    "core",
    "include",
    "rom",
    "esp32_rom/main",
    "esp32_rom/module",
    "esp32_rom/tests",
    "examples/services",
    "tests",
    "tools",
)
SKIP_DIRS = {"build", "out", "module-link", "__pycache__", ".deps", "vendor"}


def source_files():
    """Return deterministic owned-source paths without following SDK symlinks."""
    result = {"c": [], "python": []}
    for folder in SOURCE_ROOTS:
        for path in (ROOT / folder).rglob("*"):
            relative = path.relative_to(ROOT)
            if path.is_symlink() or any(
                part in SKIP_DIRS or part.startswith(".venv") for part in relative.parts
            ):
                continue
            if not path.is_file():
                continue
            kind = (
                "python"
                if path.suffix == ".py"
                else "c"
                if path.suffix in {".c", ".h", ".inc"}
                else None
            )
            if kind:
                result[kind].append(str(relative))
    return {kind: sorted(set(paths)) for kind, paths in result.items()}


def executable(name):
    """Prefer the project's isolated style environment, then the active PATH."""
    for candidate in (
        ROOT / ".venv-style" / "bin" / name,
        ROOT / ".venv-style" / "Scripts" / (name + ".exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    found = shutil.which(name)
    if not found:
        raise RuntimeError(
            "missing " + name + "; install tools/format-requirements.txt in .venv-style"
        )
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="report formatting changes without writing"
    )
    args = parser.parse_args(argv)
    try:
        files = source_files()
        clang, ruff = executable("clang-format"), executable("ruff")
        commands = [
            [clang, *(["--dry-run", "--Werror"] if args.check else ["-i"]), *files["c"]],
            [ruff, "format", *(["--check"] if args.check else []), *files["python"]],
        ]
        failed = False
        for command in commands:
            failed |= subprocess.run(command, cwd=ROOT).returncode != 0
        if not failed:
            print(
                "Format "
                + ("check" if args.check else "write")
                + " passed: "
                + str(len(files["c"]))
                + " C/header/include files, "
                + str(len(files["python"]))
                + " Python files"
            )
        return int(failed)
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
