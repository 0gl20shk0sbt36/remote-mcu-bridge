#!/usr/bin/env python3
"""Discover backend/board contracts and dispatch isolated build drivers."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES = {"resident-update", "module-runtime", "wifi-management", "uart", "spi"}
IDENTIFIER = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def text(value, field):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{field} must be a nonempty string")
    return value


def local(root, value):
    text(value, "path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("path must be relative without traversal")
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("path escapes its root")
    return path


def strings(value, field, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(
            f"{field} must be a list" + (" with at least one entry" if nonempty else "")
        )
    for entry in value:
        text(entry, field)
    if len(set(value)) != len(value):
        raise ValueError(f"{field} must not contain duplicates")
    return value


def identifier(value, field):
    if not IDENTIFIER.fullmatch(text(value, field)):
        raise ValueError(f"invalid {field}")
    return value


def validate(item, root, path, kind):
    if not isinstance(item, dict):
        raise ValueError("descriptor must be an object")
    if type(item.get("schema_version")) is not int or item["schema_version"] != 1:
        raise ValueError("schema_version must be integer 1")
    ident = identifier(item.get("id"), "id")
    if ident != path.parent.name:
        raise ValueError("mismatched id/directory")
    caps = strings(item.get("capabilities"), "capabilities")
    if not set(caps) <= CAPABILITIES:
        raise ValueError("unknown capabilities")
    if "description" in item:
        text(item["description"], "description")
    if kind == "backends":
        text(item.get("description"), "description")
        if not local(root, item.get("implementation")).is_dir():
            raise ValueError("missing implementation")
        if not local(path.parent, item.get("driver")).is_file():
            raise ValueError("missing build driver")
        req = item.get("requirements")
        if not isinstance(req, dict) or not isinstance(req.get("sdk"), dict):
            raise ValueError("requirements and requirements.sdk must be objects")
        for field in ("name", "version", "environment"):
            text(req["sdk"].get(field), "requirements.sdk." + field)
        strings(req.get("toolchain"), "requirements.toolchain", nonempty=True)
        text(req.get("python"), "requirements.python")
    else:
        identifier(item.get("backend"), "backend")
        text(item.get("target"), "target")
        for default in strings(item.get("defaults"), "defaults", nonempty=True):
            if not local(root, default).is_file():
                raise ValueError("missing defaults")
        pins = item.get("pins")
        if not isinstance(pins, dict):
            raise ValueError("pins must be an object")
        for name, pin in pins.items():
            text(name, "pin name")
            if type(pin) is not int or pin < -1:
                raise ValueError("pins must be integers >= -1 (not booleans)")
        if "target_device" in item:
            text(item["target_device"], "target_device")
    return ident


def discover(root, kind):
    if kind not in ("backends", "boards"):
        raise ValueError("unknown registry kind")
    root = Path(root).resolve()
    result = {}
    filename = "backend.json" if kind == "backends" else "board.json"
    for path in sorted((root / kind).glob("*/" + filename)):
        try:
            local(root, str(path.relative_to(root)))
            item = json.loads(path.read_text())
            ident = validate(item, root, path, kind)
            if ident in result:
                raise ValueError("duplicate id")
            result[ident] = (item, path)
        except (ValueError, OSError) as exc:
            raise ValueError(f"{path}: {exc}") from exc
    return result


def compatible(backend, board):
    if board["backend"] != backend["id"] or not set(board["capabilities"]) <= set(
        backend["capabilities"]
    ):
        raise ValueError("incompatible board/backend")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["backends", "boards", "configure", "build"])
    parser.add_argument("--backend", default="esp-idf")
    parser.add_argument("--board", default="esp32c3-stm32f103")
    parser.add_argument("--profile", choices=["safe", "compile-enabled"], default="safe")
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument(
        "--user-config", type=Path, help="private defaults file; final override authority"
    )
    parser.add_argument(
        "--credentials-file",
        type=Path,
        help="private ssid/password(/token) file, e.g. ~/wifi.pw; overrides network macros",
    )
    args = parser.parse_args(argv)
    try:
        backends = discover(ROOT, "backends")
        boards = discover(ROOT, "boards")
        for board, _ in boards.values():
            if board["backend"] not in backends:
                raise ValueError("unknown board backend: " + board["backend"])
            compatible(backends[board["backend"]][0], board)
        if args.command in ("backends", "boards"):
            print(
                json.dumps(
                    [v[0] for v in (backends if args.command == "backends" else boards).values()],
                    indent=2,
                )
            )
            return 0
        if args.backend not in backends or args.board not in boards:
            raise ValueError("unknown backend or board selection")
        backend, bpath = backends[args.backend]
        board, board_path = boards[args.board]
        compatible(backend, board)
        build = (
            args.build_dir or ROOT / "out" / args.backend / args.board / args.profile
        ).resolve()
        if build == ROOT or ROOT.is_relative_to(build):
            raise ValueError("unsafe build directory")
        cmd = [
            sys.executable,
            str(local(bpath.parent, backend["driver"])),
            args.command,
            "--root",
            str(ROOT),
            "--backend",
            str(bpath),
            "--board",
            str(board_path),
            "--build-dir",
            str(build),
            "--profile",
            args.profile,
        ]
        if args.user_config:
            cmd += ["--user-config", str(args.user_config.resolve())]
        if args.credentials_file:
            cmd += ["--credentials-file", str(args.credentials_file.resolve())]
        return subprocess.call(cmd)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    sys.exit(main())
