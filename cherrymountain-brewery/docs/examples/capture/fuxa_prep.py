"""Copy a generated FUXA project with its device addresses on loopback.

Only the address fields change (the stand-in plant, fuxa_plant.mjs, listens
there); every view, tag, alarm, and script is the generated file's.

    python fuxa_prep.py SRC.json DST.json BACNET_IFACE

FUXA's BACnet driver binds to a non-loopback address of the host (it ignores
loopback), so BACNET_IFACE is one of the host's own addresses. Its broadcast
address is 127.0.0.2, where the stand-in plant's first BACnet server listens.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

#: device name: address FUXA connects to. FUXA ignores a Modbus device's `port`
#: property, so the port is part of the address.
ADDRESS: dict[str, str] = {
    "plc-brewhouse": "opc.tcp://127.0.0.1:4840/openplc/opcua",
    "plc-cellar": "127.0.0.1:1502",
    "plc-utilities": "127.0.0.1:1503",
    "plc-power": "127.0.0.1:1504",
    "plc-packaging": "127.0.0.1",
}


def main(argv: list[str]) -> int:
    """Rewrite the addresses and save the project."""
    src, dst, iface = Path(argv[1]), Path(argv[2]), argv[3]
    project = json.loads(src.read_text(encoding="utf-8"))
    for dev in project["devices"].values():
        prop = dev["property"]
        if dev["name"] in ADDRESS:
            prop["address"] = ADDRESS[dev["name"]]
            if "port" in prop and dev["type"] == "ModbusTCP":
                prop["port"] = ADDRESS[dev["name"]].rsplit(":", 1)[1]
        elif dev["type"] == "BACnet":
            prop["address"] = iface
            prop["broadcastAddress"] = "127.0.0.2"
    dst.write_text(json.dumps(project), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
