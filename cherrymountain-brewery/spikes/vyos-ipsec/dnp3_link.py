#!/usr/bin/env python3
"""Minimal DNP3 link-layer outstation and probe (runs ON a guest, stdlib only).

Proves DNP3 (TCP 20000) crosses the IPsec tunnel without needing ot-sim:
  rtu-der:        ./dnp3_link.py serve [PORT]
  utility-derms:  ./dnp3_link.py probe HOST [PORT]

The probe sends a Request Link Status frame (primary, function 9) and expects a
Link Status frame (secondary, function 11) with a valid header CRC. The server
answers Request Link Status and Reset Link States. This is only the link
layer; the real DNP3 master/outstation test is the otsim-noble spike.
"""

import socket
import struct
import sys

START: bytes = b"\x05\x64"
PORT: int = 20000
MASTER: int = 3
OUTSTATION: int = 4


def crc_dnp(data: bytes) -> bytes:
    """DNP3 CRC-16 (poly 0x3D65 reflected), complemented, low byte first."""
    crc: int = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA6BC if crc & 1 else crc >> 1
    return struct.pack("<H", ~crc & 0xFFFF)


def frame(ctrl: int, dest: int, src: int) -> bytes:
    head: bytes = START + bytes([5, ctrl]) + struct.pack("<HH", dest, src)
    return head + crc_dnp(head)


def parse(data: bytes) -> tuple[int, int, int] | None:
    """Return (function code, dest, src) of a valid header, else None."""
    if len(data) < 10 or data[:2] != START or crc_dnp(data[:8]) != data[8:10]:
        return None
    dest, src = struct.unpack("<HH", data[4:8])
    return data[3] & 0x0F, dest, src


def serve(port: int) -> int:
    srv: socket.socket = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))  # noqa: S104
    srv.listen(8)
    print(f"outstation link layer on :{port}", flush=True)
    while True:
        conn, peer = srv.accept()
        with conn:
            data: bytes = conn.recv(64)
            parsed = parse(data)
            print(f"{peer[0]}: {data.hex()} -> {parsed}", flush=True)
            if parsed and parsed[0] in (0, 9):
                code: int = 11 if parsed[0] == 9 else 0
                conn.sendall(frame(code, parsed[2], parsed[1]))


def probe(host: str, port: int) -> int:
    with socket.create_connection((host, port), timeout=5) as conn:
        conn.settimeout(5)
        conn.sendall(frame(0xC9, OUTSTATION, MASTER))
        reply = parse(conn.recv(64))
    if reply is None or reply[0] != 11:
        print(f"FAIL: no valid link status from {host}:{port}: {reply}")
        return 1
    print(f"PASS: DNP3 link status from {host}:{port} (dest {reply[1]}, src {reply[2]})")
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "serve":
        sys.exit(serve(int(sys.argv[2]) if len(sys.argv) > 2 else PORT))
    if len(sys.argv) >= 3 and sys.argv[1] == "probe":
        sys.exit(probe(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else PORT))
    print(__doc__)
    sys.exit(2)
