#!/usr/bin/env python3
"""Modbus RTU round trip over the serial link.

Run as server on one guest, client on the other, each against its pty:
  glycol-chiller:  ./rtu_check.py server /dev/ttyS10
  rio-utilities:   ./rtu_check.py client /dev/ttyS10
Install: pip install pymodbus pyserial
Pass: the client reads the register values the server holds (and CRC is valid).
TODO(verify): pymodbus 3.x API drift (RTU server startup helpers and the
slave/device_id argument name changed across 3.x); adjust to the installed
version. For the real test, use an ot-sim Modbus RTU config instead (ot-sim
modbus module serial settings, TODO(verify): option names).
"""
import sys


def server(dev: str) -> int:
    from pymodbus.datastore import (
        ModbusDeviceContext,  # TODO(verify): named ModbusSlaveContext before 3.10
        ModbusSequentialDataBlock,
        ModbusServerContext,
    )
    from pymodbus.server import StartSerialServer

    ctx = ModbusDeviceContext(hr=ModbusSequentialDataBlock(0, [111, 222, 333, 444]))
    StartSerialServer(
        context=ModbusServerContext(devices={1: ctx}, single=False),
        port=dev,
        baudrate=9600,
        bytesize=8,
        parity="N",
        stopbits=1,
        framer="rtu",
    )
    return 0


def client(dev: str) -> int:
    from pymodbus.client import ModbusSerialClient

    c = ModbusSerialClient(port=dev, baudrate=9600, bytesize=8, parity="N", stopbits=1, timeout=3)
    if not c.connect():
        print(f"FAIL: cannot open {dev}")
        return 1
    try:
        rr = c.read_holding_registers(0, count=4, device_id=1)
    except TypeError:
        rr = c.read_holding_registers(0, count=4, slave=1)
    c.close()
    if rr.isError() or rr.registers != [111, 222, 333, 444]:
        print(f"FAIL: {rr}")
        return 1
    print("PASS: RTU read", rr.registers)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("server", "client"):
        print(__doc__)
        sys.exit(2)
    sys.exit((server if sys.argv[1] == "server" else client)(sys.argv[2]))
