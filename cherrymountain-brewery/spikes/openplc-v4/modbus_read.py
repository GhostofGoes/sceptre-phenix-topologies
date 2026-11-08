#!/usr/bin/env python3
"""Read holding registers and coils from a PLC or ot-sim device with pymodbus.

Usage: modbus_read.py HOST [--port 502] [--addr 0] [--count 4] [--unit 1]
Install: pip install pymodbus
Verified with pymodbus 3.16.2 (`device_id=` keyword) against OpenPLC v4.3.2
`modbus_slave`. A read past the registers the program's image exposes returns
Exception 2 (Illegal Data Address), so keep --count within the located
variables (`%QW` count plus `%MW` count, ...).
"""
import argparse
import sys

from pymodbus.client import ModbusTcpClient

DEFAULT_PORT: int = 502
DEFAULT_COUNT: int = 4


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--addr", type=int, default=0)
    ap.add_argument("--count", type=int, default=DEFAULT_COUNT)
    ap.add_argument("--unit", type=int, default=1)
    a = ap.parse_args()

    client = ModbusTcpClient(a.host, port=a.port, timeout=3)
    if not client.connect():
        print(f"FAIL: cannot connect to {a.host}:{a.port}")
        return 1
    try:
        # pymodbus 3.10+ renamed slave= to device_id=; try the new name first.
        try:
            rr = client.read_holding_registers(a.addr, count=a.count, device_id=a.unit)
        except TypeError:
            rr = client.read_holding_registers(a.addr, count=a.count, slave=a.unit)
        if rr.isError():
            print(f"FAIL: {rr}")
            return 1
        print(f"holding[{a.addr}..{a.addr + a.count - 1}] = {rr.registers}")
        try:
            rc = client.read_coils(a.addr, count=8, device_id=a.unit)
        except TypeError:
            rc = client.read_coils(a.addr, count=8, slave=a.unit)
        if not rc.isError():
            print(f"coils[{a.addr}..{a.addr + 7}] = {rc.bits[:8]}")
        print("PASS")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
