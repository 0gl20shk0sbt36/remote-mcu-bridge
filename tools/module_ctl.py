#!/usr/bin/env python3
"""Resident supervisor management; authenticated trusted-LAN TCP, not TLS."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sys

MAX_REPLY = 16384


def response(stream):
    line = stream.readline(MAX_REPLY + 1)
    if not line.endswith(b"\n") or len(line) > MAX_REPLY:
        raise RuntimeError("missing or overlong supervisor response")
    obj = json.loads(line)
    if not isinstance(obj, dict):
        raise RuntimeError("invalid supervisor response")
    if not obj.get("ok"):
        raise RuntimeError(str(obj.get("error", "supervisor rejected request")))
    return obj


def request(host, port, token, op, image=None, timeout=180, fault_seq=None):
    """Exchange one management request, preserving the two-phase upload gate.

    The ACK sequence identifies exactly one retained fault; confirming it does
    not authorize service execution. Transport failure never implies commit.
    """
    header = {"token": token, "op": op}
    if op == "ack-fault":
        if type(fault_seq) is not int or not 1 <= fault_seq <= 0xFFFFFFFF:
            raise ValueError("ack-fault requires a nonzero uint32 fault sequence")
        header["seq"] = fault_seq
    elif fault_seq is not None:
        raise ValueError("fault sequence is only valid for ack-fault")
    if image is not None:
        if not image or len(image) > 0x100000 - 4096:
            raise ValueError("module size outside single-slot bounds")
        header.update(size=len(image), sha256=hashlib.sha256(image).hexdigest())
    with socket.create_connection((host, port), timeout=min(timeout, 10)) as sock:
        sock.settimeout(timeout)
        sock.sendall(json.dumps(header, separators=(",", ":")).encode() + b"\n")
        with sock.makefile("rb") as stream:
            result = response(stream)
            if image is not None:
                if not result.get("ready"):
                    raise RuntimeError("supervisor did not authorize upload")
                sock.sendall(image)
                result = response(stream)
            return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "op",
        choices=[
            "status",
            "get-fault",
            "ack-fault",
            "enter-update",
            "upload",
            "restart",
            "clear-fault",
            "test-hang",
        ],
    )
    parser.add_argument("image", nargs="?")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=3337)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument(
        "--fault-seq", type=int, help="sequence returned by get-fault; required for ack-fault"
    )
    args = parser.parse_args(argv)
    if (args.op == "ack-fault") != (args.fault_seq is not None):
        parser.error("only ack-fault requires --fault-seq")
    if args.fault_seq is not None and not 1 <= args.fault_seq <= 0xFFFFFFFF:
        parser.error("--fault-seq must be a nonzero uint32")
    if (args.op == "upload") != bool(args.image):
        parser.error("only upload requires an image path")
    try:
        token = (
            args.token_file.read_text().strip()
            if args.token_file
            else os.environ.get("STM32_ROM_TOKEN")
        )
        if not token:
            token = Path(__file__).with_name("token.txt").read_text().strip()
        if not token:
            raise ValueError("empty token forbidden")
        ops = {"upload": "module-upload", "restart": "esp-restart", "test-hang": "module-test-hang"}
        data = Path(args.image).read_bytes() if args.image else None
        result = request(
            args.host,
            args.port,
            token,
            ops.get(args.op, args.op),
            data,
            args.timeout,
            fault_seq=args.fault_seq,
        )
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
