#!/usr/bin/env python3
"""Write a FUXA project JSON (for secure_flow.sh PROJECT=...) with one device
per protocol, each with one tag, pointed at the spike PLC and BACnet device.

Usage: make_project.py OUT.json --plc 10.20.40.10 --bacnet 10.20.60.13 \
           --local-ip 10.20.30.10 [--bacnet-only] [--broadcast-address none|ADDR]
  --bacnet-only keeps just the BACnet device (no PLC needed). By default the
  device's `broadcastAddress` is the --bacnet IP; node-bacnet sends its Who-Is
  to that address, so with a unicast address nothing is broadcast. Pass
  `--broadcast-address none` to leave the property out (the Who-Is then goes to
  255.255.255.255, which is what a FUXA that does not know the device does).
Install: pip install pydantic
Schema read from FUXA v1.3.5 client/src/app/_models/device.ts and the drivers
in server/runtime/devices/*. Tag address formats:
  OPCUA   node id string        ns=2;s=PLC.cnt
  SiemensS7  DB<n>.DBW<byte>    DB1.DBW0  (type Int)
  ModbusTCP  address is 1-based, memaddress 400000 = holding registers
  BACnet  <objectType>-<instance>, memaddress = remote device instance
          (BACnet property.address is the LOCAL interface IP, not the remote)
"""
import argparse
import json
from pathlib import Path

from pydantic import BaseModel, Field

PROJECT_VERSION: str = "1.00"  # what a fresh v1.3.5 GET /api/project reports
POLL_MS: int = 1000


class Tag(BaseModel):
    id: str
    name: str
    type: str
    address: str
    memaddress: str = ""
    label: str = ""
    divisor: int = 1


class Device(BaseModel):
    id: str
    name: str
    type: str
    enabled: bool = True
    polling: int = POLL_MS
    property: dict[str, object]
    tags: dict[str, Tag] = Field(default_factory=dict)

    def with_tag(self, tag: Tag) -> "Device":
        self.tags[tag.id] = tag
        return self


class View(BaseModel):
    id: str
    name: str
    profile: dict[str, object] = {"width": 1024, "height": 768, "bkcolor": "#ffffffff"}
    items: dict[str, object] = {}
    variables: dict[str, object] = {}
    svgcontent: str = ""


def build(plc: str, bacnet: str, local_ip: str, bacnet_only: bool = False, broadcast: str | None = None) -> dict[str, object]:
    """broadcast: None = the --bacnet IP (legacy), "none" = omit the property, else that address."""
    bac_property: dict[str, object] = {"address": local_ip}
    if broadcast != "none":
        bac_property["broadcastAddress"] = broadcast or bacnet
    bacnet_device = Device(
        id="d_bacnet", name="bas-bacnet", type="BACnet", property=bac_property,
    ).with_tag(Tag(id="t_bac_co2", name="bac_co2", type="Real", address="0-0", memaddress="3003"))
    if bacnet_only:
        view_only = View(id="v_spike", name="Spike")
        server = Device(id="0", name="FUXA Server", type="FuxaServer", property={})
        return {
            "version": PROJECT_VERSION,
            "devices": {d.id: d.model_dump() for d in (server, bacnet_device)},
            "hmi": {"views": [view_only.model_dump()]},
        }
    devices = [
        # POST /api/project replaces everything, including the built-in
        # FuxaServer device; re-add it or the project loses it.
        Device(id="0", name="FUXA Server", type="FuxaServer", property={}),
        Device(
            id="d_opcua", name="plc-opcua", type="OPCUA",
            property={"address": f"opc.tcp://{plc}:4840/openplc/opcua"},
        ).with_tag(Tag(id="t_opcua_cnt", name="opcua_cnt", type="Int16", address="ns=2;s=PLC.cnt")),
        Device(
            id="d_s7", name="plc-s7", type="SiemensS7",
            property={"address": plc, "rack": 0, "slot": 1},
        ).with_tag(Tag(id="t_s7_cnt", name="s7_cnt", type="Int", address="DB1.DBW0")),
        Device(
            id="d_modbus", name="plc-modbus", type="ModbusTCP",
            property={"address": plc, "port": "502", "slaveid": "1", "delay": 10},
        ).with_tag(Tag(id="t_mb_cnt", name="mb_cnt", type="Int16", address="1", memaddress="400000")),
        bacnet_device,
    ]
    view = View(id="v_spike", name="Spike")
    return {
        "version": PROJECT_VERSION,
        "devices": {d.id: d.model_dump() for d in devices},
        "hmi": {"views": [view.model_dump()]},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--plc", default="127.0.0.1")
    ap.add_argument("--bacnet", required=True)
    ap.add_argument("--local-ip", required=True, help="FUXA host interface IP for BACnet")
    ap.add_argument("--bacnet-only", action="store_true", help="only the FuxaServer and BACnet devices")
    ap.add_argument("--broadcast-address", default=None, help='BACnet broadcastAddress property; "none" omits it')
    a = ap.parse_args()
    a.out.write_text(json.dumps(build(a.plc, a.bacnet, a.local_ip, a.bacnet_only, a.broadcast_address), indent=1) + "\n")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
