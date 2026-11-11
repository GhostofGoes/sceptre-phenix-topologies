#!/usr/bin/env python3
"""Dependency-free DNP3 reachability probe (outstation or FEP on TCP 20000).

Sends a link-layer REQUEST_LINK_STATUS and expects a LINK_STATUS reply. This
proves the DNP3 module is listening and framing correctly; it does not prove
an application-layer integrity poll (use the upstream master for that, see
README step 5). Also usable for the IPsec spike (vyos-ipsec).

Usage: dnp3_link_probe.py HOST [--port 20000] [--dest 1024] [--src 1]
Defaults match ot-sim's example configs (outstation local-address 1024, master
remote-address 1). Verified against ot-sim-dnp3-module on Ubuntu 24.04: the
LINK_STATUS reply arrives only when --dest equals the outstation's configured
local-address; any other address (for example 10) gets no reply.
"""
import argparse
import socket
import struct
import sys


DEFAULT_PORT: int = 20000
DEFAULT_DEST: int = 1024
DEFAULT_SRC: int = 1


def crc16_dnp(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA6BC if crc & 1 else crc >> 1
    return (~crc) & 0xFFFF


def frame(dest: int, src: int) -> bytes:
    # start 0x0564, length 5 (header only), control 0xC9 = DIR|PRM|func 9.
    head = bytes([0x05, 0x64, 0x05, 0xC9]) + struct.pack("<HH", dest, src)
    return head + struct.pack("<H", crc16_dnp(head))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--dest", type=int, default=DEFAULT_DEST, help="outstation link address")
    ap.add_argument("--src", type=int, default=DEFAULT_SRC, help="master link address")
    a = ap.parse_args()
    try:
        with socket.create_connection((a.host, a.port), timeout=5) as s:
            s.settimeout(5)
            s.sendall(frame(a.dest, a.src))
            resp = s.recv(64)
    except OSError as exc:
        print(f"FAIL: {exc}")
        return 1
    print("reply:", resp.hex())
    ok = len(resp) >= 10 and resp[:2] == b"\x05\x64" and (resp[3] & 0x0F) == 0x0B
    print("PASS: LINK_STATUS received" if ok else "FAIL: no LINK_STATUS reply")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
