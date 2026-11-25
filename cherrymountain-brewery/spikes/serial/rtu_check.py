#!/usr/bin/env python3
"""Modbus RTU round trip over the serial link (runs ON a guest, stdlib only).

Server on one guest, client on the other, each against its pty:
  glycol-chiller:  ./rtu_check.py server /dev/ttyS10
  rio-utilities:   ./rtu_check.py client /dev/ttyS10 [--polls 600 --interval 0.5]

The server answers unit 1: function 3 (read holding registers) and function 6
(write single register) over four registers, with a valid CRC-16. The client
reads, writes, reads back, then polls and reports the failure rate.

This deliberately avoids pymodbus: guests have no internet in a phenix range.
For ot-sim's own Modbus RTU module see the README (TODO(verify) there).
"""

import argparse
import os
import select
import struct
import sys
import time

UNIT: int = 1


def crc16(data: bytes) -> bytes:
    """Modbus CRC-16, low byte first."""
    crc: int = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)


def with_crc(frame: bytes) -> bytes:
    return frame + crc16(frame)


def read_frame(fd: int, timeout: float, idle: float = 0.05) -> bytes:
    """Read one frame: wait up to timeout for the first byte, then until the line is idle."""
    buf: bytes = b""
    deadline: float = time.monotonic() + timeout
    while True:
        wait: float = idle if buf else max(0.0, deadline - time.monotonic())
        ready, _, _ = select.select([fd], [], [], wait)
        if not ready:
            return buf
        chunk: bytes = os.read(fd, 256)
        if not chunk:
            return buf
        buf += chunk


def open_pty(dev: str) -> int:
    return os.open(dev, os.O_RDWR | os.O_NOCTTY)


def serve(dev: str) -> int:
    regs: list[int] = [111, 222, 333, 444]
    fd: int = open_pty(dev)
    print(f"server on {dev}, unit {UNIT}, registers {regs}", flush=True)
    while True:
        frame: bytes = read_frame(fd, 3600)
        if len(frame) < 8 or frame[:-2] and crc16(frame[:-2]) != frame[-2:]:
            if frame:
                print(f"bad frame {frame.hex()}", flush=True)
            continue
        unit, func = frame[0], frame[1]
        if unit != UNIT:
            continue
        if func == 3:
            addr, count = struct.unpack(">HH", frame[2:6])
            if addr + count > len(regs):
                resp: bytes = bytes([unit, func | 0x80, 2])
            else:
                resp = bytes([unit, func, count * 2]) + b"".join(struct.pack(">H", r) for r in regs[addr : addr + count])
        elif func == 6:
            addr, value = struct.unpack(">HH", frame[2:6])
            if addr >= len(regs):
                resp = bytes([unit, func | 0x80, 2])
            else:
                regs[addr] = value
                resp = frame[:6]
        else:
            resp = bytes([unit, func | 0x80, 1])
        os.write(fd, with_crc(resp))


def transact(fd: int, request: bytes, timeout: float = 2.0) -> bytes | None:
    os.write(fd, with_crc(request))
    resp: bytes = read_frame(fd, timeout)
    if len(resp) < 5 or crc16(resp[:-2]) != resp[-2:]:
        return None
    return resp


def read_regs(fd: int, addr: int, count: int) -> list[int] | None:
    resp: bytes | None = transact(fd, struct.pack(">BBHH", UNIT, 3, addr, count))
    if resp is None or resp[1] != 3 or resp[2] != count * 2:
        return None
    return list(struct.unpack(f">{count}H", resp[3 : 3 + count * 2]))


def client(dev: str, polls: int, interval: float) -> int:
    fd: int = open_pty(dev)
    first: list[int] | None = read_regs(fd, 0, 4)
    if first != [111, 222, 333, 444]:
        print(f"FAIL: first read returned {first}")
        return 1
    print(f"PASS: RTU read {first}", flush=True)
    wrote: bytes | None = transact(fd, struct.pack(">BBHH", UNIT, 6, 2, 999))
    again: list[int] | None = read_regs(fd, 0, 4)
    if wrote is None or again != [111, 222, 999, 444]:
        print(f"FAIL: write then read returned {again}")
        return 1
    print(f"PASS: RTU write visible on read-back {again}", flush=True)
    failures: int = 0
    for _ in range(polls):
        if read_regs(fd, 0, 4) is None:
            failures += 1
        time.sleep(interval)
    pct: float = 100.0 * failures / max(polls, 1)
    print(f"POLLS: {polls} interval {interval}s failures {failures} ({pct:.2f}%)", flush=True)
    print("PASS: poll loss under 1%" if pct < 1.0 else "FAIL: poll loss 1% or more")
    return 0 if pct < 1.0 else 1


def main() -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["server", "client"])
    parser.add_argument("device")
    parser.add_argument("--polls", type=int, default=600)
    parser.add_argument("--interval", type=float, default=0.5)
    args: argparse.Namespace = parser.parse_args()
    return serve(args.device) if args.mode == "server" else client(args.device, args.polls, args.interval)


if __name__ == "__main__":
    sys.exit(main())
