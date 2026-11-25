#!/usr/bin/env python3
"""Modbus/TCP reads and writes against an ot-sim node's loopback server (runs ON a guest, stdlib only).

  otsim_check.py watch [--port 5502] [--seconds 60]   read input 30000 every second; report values seen
  otsim_check.py write [--port 5502] ADDR VALUE       write one holding register (function 6)
  otsim_check.py read  [--port 5502] ADDR             read one holding register (function 3)

Addresses are the raw register addresses ot-sim was configured with (input 30000, holding 40000).
Prints one JSON line.
"""

import argparse
import json
import socket
import struct
import sys
import time

UNIT: int = 1
INPUT_ADDR: int = 30000


def transact(port: int, function: int, addr: int, value_or_count: int) -> bytes:
    pdu: bytes = struct.pack(">BHH", function, addr, value_or_count)
    frame: bytes = struct.pack(">HHHB", 1, 0, len(pdu) + 1, UNIT) + pdu
    with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
        sock.sendall(frame)
        head: bytes = sock.recv(7)
        length: int = struct.unpack(">H", head[4:6])[0]
        body: bytes = b""
        while len(body) < length - 1:
            chunk: bytes = sock.recv(length - 1 - len(body))
            if not chunk:
                break
            body += chunk
    if body[0] & 0x80:
        raise ValueError(f"exception {body[1]} for function {function}")
    return body


def read_regs(port: int, function: int, addr: int) -> int:
    body: bytes = transact(port, function, addr, 1)
    return struct.unpack(">H", body[2:4])[0]


def main() -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["watch", "write", "read"])
    parser.add_argument("args", nargs="*", type=int)
    parser.add_argument("--port", type=int, default=5502)
    parser.add_argument("--seconds", type=int, default=60)
    args: argparse.Namespace = parser.parse_args()
    if args.mode == "watch":
        values: list[int] = []
        errors: int = 0
        for _ in range(args.seconds):
            try:
                values.append(read_regs(args.port, 4, INPUT_ADDR))
            except (OSError, ValueError, struct.error, IndexError):
                errors += 1
            time.sleep(1)
        print(json.dumps({"samples": len(values), "errors": errors, "distinct": len(set(values)), "first": values[:5], "last": values[-5:]}))
        return 0
    if args.mode == "write":
        transact(args.port, 6, args.args[0], args.args[1])
        print(json.dumps({"wrote": args.args}))
        return 0
    print(json.dumps({"value": read_regs(args.port, 3, args.args[0])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
