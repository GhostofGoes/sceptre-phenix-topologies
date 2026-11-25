#!/usr/bin/env python3
"""Probe the OpenPLC v4 opcua and s7comm plugins with Python stand-ins for the
stacks FUXA uses (node-opcua, node-snap7).

Usage: opcua_s7_probe.py PLC_HOST [--opcua-port 4840] [--opcua-path /openplc/opcua]
                         [--s7-port 102] [--s7-db 1]
Install: pip install asyncua python-snap7 pydantic
Verified with asyncua 2.1.0 and python-snap7 3.4.0 against OpenPLC v4.3.2.
The OPC UA server also answers on the bare URL (no path). The s7comm plugin
exposes only the DB numbers listed in conf/s7comm.json (fixture: DB1 = %QW,
DB2 = %IW). The decisive test is still FUXA's own device connection.
"""
import argparse
import asyncio
import sys

from asyncua import Client
from pydantic import BaseModel


class ProbeResult(BaseModel):
    protocol: str
    ok: bool
    detail: str


async def opcua(host: str, port: int, path: str) -> ProbeResult:
    url = f"opc.tcp://{host}:{port}{path}"
    try:
        async with Client(url=url, timeout=5) as c:
            kids = await c.nodes.objects.get_children()
            names = [(await k.read_browse_name()).Name for k in kids]
            return ProbeResult(protocol="opcua", ok=True, detail=f"{url}: Objects children {names}")
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(protocol="opcua", ok=False, detail=f"{url}: {exc!r}")


def s7(host: str, port: int, db: int) -> ProbeResult:
    try:
        import snap7

        cl = snap7.client.Client()
        cl.connect(host, 0, 1, port)  # rack 0, slot 1
        data = cl.db_read(db, 0, 4)
        cl.disconnect()
        return ProbeResult(protocol="s7", ok=True, detail=f"{host}:{port} DB{db}[0:4]={bytes(data).hex()}")
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(protocol="s7", ok=False, detail=f"{host}:{port}: {exc!r}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--opcua-port", type=int, default=4840)
    ap.add_argument("--opcua-path", default="/openplc/opcua")
    ap.add_argument("--s7-port", type=int, default=102)
    ap.add_argument("--s7-db", type=int, default=1)
    a = ap.parse_args()
    results = [asyncio.run(opcua(a.host, a.opcua_port, a.opcua_path)), s7(a.host, a.s7_port, a.s7_db)]
    for r in results:
        print(("ok   " if r.ok else "FAIL ") + r.protocol, r.detail)
    ok = all(r.ok for r in results)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
