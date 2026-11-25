#!/usr/bin/env python3
"""Render rio-cellar's ot-sim config with the real phenix-apps ``ot-sim`` app.

The generated ``plant/generated/ci/phenix/ot-sim.yaml`` is the scenario's ot-sim
app block. This script feeds that block to ``phenix_apps.apps.otsim.app.OTSim``
(``pre_start``) with a minimal two-node topology, exactly as ``phenix`` would,
and copies the resulting ``<host>.xml`` out of the experiment directory.

Needs ``phenix-apps`` installed (``pip install <sceptre-phenix-apps>/src/python``).
With ``--hand-render`` it does not: :func:`hand_render` writes the same XML
for a Modbus ``fd-server`` itself. ``test_render_otsim.py`` checks that both
paths agree whenever phenix-apps is installed, so the fallback cannot drift
silently. The integration test uses the app and falls back only if the app
cannot be imported.

Usage:
    python render_otsim.py --ot-sim-yaml plant/generated/ci/phenix/ot-sim.yaml \\
        --rio-address 10.20.12.10 --broker-address 127.0.0.1 --out DIR [--hand-render]
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class Node(BaseModel):
    """One addressed topology node."""

    hostname: str
    interface: str
    address: str

    def topology(self) -> dict[str, Any]:
        """The node as phenix's topology spec has it."""
        return {
            "general": {"hostname": self.hostname},
            "hardware": {"os_type": "linux"},
            "network": {
                "interfaces": [
                    {"name": self.interface, "address": self.address, "mask": 16, "vlan": "OT"}
                ]
            },
        }


def render(app_block: dict[str, Any], nodes: list[Node], out: Path) -> list[Path]:
    """Run the app's pre-start stage and return the XML files it wrote."""
    # Imported late so --help works without phenix-apps installed.
    from phenix_apps.apps.otsim.app import OTSim  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmp:
        experiment: dict[str, Any] = {
            "spec": {
                "experimentName": "cm-ci-slice",
                "baseDir": str(Path(tmp) / "exp"),
                "topology": {"nodes": [n.topology() for n in nodes]},
                "scenario": {"apps": [app_block]},
            }
        }
        sys.stdin = io.StringIO(json.dumps(experiment))
        app: Any = OTSim("ot-sim", "pre-start")
        app.pre_start()
        out.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for xml in sorted(Path(app.otsim_dir).glob("*.xml")):
            dest: Path = out / xml.name
            shutil.copyfile(xml, dest)
            written.append(dest)
    return written


#: ot-sim register type and base address for each phenix point type.
SPACES: dict[str, tuple[str, int]] = {
    "binary-read-write": ("coil", 1),
    "binary-read": ("discrete", 10001),
    "analog-read": ("input", 30001),
    "analog-read-write": ("holding", 40001),
}


def _text(parent: ET.Element, tag: str, text: str) -> ET.Element:
    child: ET.Element = ET.SubElement(parent, tag)
    child.text = text
    return child


def hand_render(app_block: dict[str, Any], rio_host: str, nodes: list[Node]) -> str:
    """The phenix ``ot-sim`` app's output for one Modbus ``fd-server``, by hand.

    Mirrors ``phenix_apps.apps.otsim`` (pre-start) for the cases the cellar
    slice uses: device points numbered per register space in host device
    order, scaling written negated, one HELICS subscription per point, and an
    endpoint that carries the writable points. Everything else the app does
    (DNP3, FEPs, clients, Node-RED, TLS) is out of scope.
    """
    meta: dict[str, Any] = app_block["metadata"]
    host: dict[str, Any] = next(h for h in app_block["hosts"] if h["hostname"] == rio_host)
    modbus: dict[str, Any] = host["metadata"]["modbus"]
    infra: dict[str, Any] = meta["infrastructures"][meta["infrastructure"]]
    helics: dict[str, Any] = meta["helics"]
    default_fed: str = helics["federate"]
    by_name: dict[str, Node] = {n.hostname: n for n in nodes}
    iface, _, port = modbus["interface"].partition(":")
    assert iface == by_name[rio_host].interface, "rio interface must be the topology's"
    broker_host, _, _ = helics["broker"]["hostname"].partition("|")

    root: ET.Element = ET.Element("ot-sim")
    bus: ET.Element = ET.SubElement(root, "message-bus")
    _text(bus, "pull-endpoint", "tcp://127.0.0.1:1234")
    _text(bus, "pub-endpoint", "tcp://127.0.0.1:5678")
    cpu: ET.Element = ET.SubElement(root, "cpu")
    _text(ET.SubElement(cpu, "api"), "endpoint", meta["cpu-module"]["api-endpoint"])
    for name, cmd in (
        ("backplane", "ot-sim-message-bus"),
        ("modbus", "ot-sim-modbus-module"),
        ("i/o", "ot-sim-io-module"),
    ):
        ET.SubElement(cpu, "module", {"name": name}).text = f"{cmd} {{{{config_file}}}}"

    server: ET.Element = ET.SubElement(
        root, "modbus", {"name": "modbus-outstation", "mode": "server"}
    )
    _text(server, "endpoint", f"{by_name[rio_host].address}:{port}")
    io: ET.Element = ET.Element("io", {"name": "helics-federate"})
    _text(io, "broker-endpoint", by_name[broker_host].address)
    _text(io, "federate-name", rio_host)
    _text(io, "federate-log-level", "SUMMARY")
    _text(io, "end-time", str(helics["end-time"]))
    updates: ET.Element = ET.Element("endpoint", {"name": f"{default_fed}/{helics['endpoint']}"})
    counters: dict[str, int] = {space: base for space, base in SPACES.values()}
    for dev in modbus["devices"]:
        fed, _, name = (
            dev["name"] if "/" in dev["name"] else f"{default_fed}/{dev['name']}"
        ).partition("/")
        for point, spec in infra[dev["type"]].items():
            kind: str = spec["type"]
            space: str = SPACES[kind][0]
            reg: ET.Element = ET.SubElement(server, "register", {"type": space})
            if kind.startswith("analog"):
                reg.set("data-type", spec["modbus"]["data-type"])
            _text(reg, "address", str(counters[space]))
            counters[space] += 1
            tag: str = f"{name}.{point}"
            _text(reg, "tag", tag)
            if kind.startswith("analog"):
                _text(reg, "scaling", str(-spec["modbus"]["scaling"]))
            sub: ET.Element = ET.SubElement(io, "subscription")
            _text(sub, "key", f"{fed}/{tag}")
            _text(sub, "tag", tag)
            _text(sub, "type", "double" if kind.startswith("analog") else "boolean")
            if kind.endswith("read-write"):
                ET.SubElement(updates, "tag", {"key": tag}).text = tag
    io.append(updates)
    root.append(io)
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode") + "\n"


def main(argv: list[str] | None = None) -> int:
    ap: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ot-sim-yaml", type=Path, required=True)
    ap.add_argument("--rio-host", default="rio-cellar")
    ap.add_argument("--rio-address", required=True)
    ap.add_argument("--broker-host", default="sim-ctl")
    ap.add_argument("--broker-address", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--hand-render",
        action="store_true",
        help="do not use phenix-apps; write the XML with hand_render()",
    )
    args: argparse.Namespace = ap.parse_args(argv)

    app_block: dict[str, Any] = yaml.safe_load(args.ot_sim_yaml.read_text())
    nodes: list[Node] = [
        Node(hostname=args.rio_host, interface="IF0", address=args.rio_address),
        Node(hostname=args.broker_host, interface="mgmt", address=args.broker_address),
    ]
    if args.hand_render:
        args.out.mkdir(parents=True, exist_ok=True)
        path: Path = args.out / f"{args.rio_host}.xml"
        path.write_text(hand_render(app_block, args.rio_host, nodes), encoding="utf-8")
        print(f"wrote {path} (hand-rendered)")
        return 0
    for path in render(app_block, nodes, args.out):
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
