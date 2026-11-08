#!/usr/bin/env python3
"""Probe the OpenPLC v4 opcua and s7comm plugins with the same stacks FUXA uses.

Usage: opcua_s7_probe.py PLC_HOST [--opcua-port 4840] [--s7-port 102]
Install: pip install asyncua python-snap7
FUXA uses node-opcua and node-snap7; these Python clients are stand-ins that
show the plugins answer (browse root, read one S7 DB). The decisive test is
still FUXA's own device connection (README step 4).
"""
import argparse
import asyncio
import sys


async def opcua(host: str, port: int) -> bool:
    from asyncua import Client

    url = f"opc.tcp://{host}:{port}"
    # TODO(verify): the plugin's real endpoint path (template default is
    # opc.tcp://localhost:4840/...); append it here if the server requires it.
    try:
        async with Client(url=url, timeout=5) as c:
            kids = await c.nodes.objects.get_children()
            print(f"OPC UA ok at {url}: {len(kids)} children under Objects")
            return True
    except Exception as exc:  # noqa: BLE001
        print(f"OPC UA FAIL at {url}: {exc!r}")
        return False


def s7(host: str, port: int) -> bool:
    try:
        import snap7

        cl = snap7.client.Client()
        cl.connect(host, 0, 1, port)  # rack 0, slot 1
        # TODO(verify): which DB number/size the s7comm plugin exposes.
        data = cl.db_read(1, 0, 4)
        print(f"S7 ok at {host}:{port}: DB1[0:4]={bytes(data).hex()}")
        cl.disconnect()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"S7 FAIL at {host}:{port}: {exc!r}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--opcua-port", type=int, default=4840)
    ap.add_argument("--s7-port", type=int, default=102)
    a = ap.parse_args()
    ok1 = asyncio.run(opcua(a.host, a.opcua_port))
    ok2 = s7(a.host, a.s7_port)
    print("PASS" if ok1 and ok2 else "FAIL")
    return 0 if ok1 and ok2 else 1


if __name__ == "__main__":
    sys.exit(main())
