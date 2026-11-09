#!/usr/bin/env python3
"""Build the FUXA project JSON of a FUXA host from plantgen output.

Reads ``plant/generated/<variant>/fuxa/<host>-tags.json`` and ``alarms.json``.

* ``ci`` (scada-fuxa only): one Modbus TCP device (``plc-cellar``), one Cellar
  view for FV-101 to FV-104, and one FUXA alarm per PLC alarm bit, written to
  ``fuxa/scada-fuxa-project.json`` (the ci topology injects it).
* ``full`` and ``lite``, for ``scada-fuxa`` and ``bas-fuxa``: every device of
  the tag list (Modbus TCP, OPC UA, S7, BACnet), a generated view per device
  (plus the Cellar view where its tags exist), and every alarm whose tag is in
  the project: PLC alarm bits (active at 1) and the alarms FUXA evaluates itself
  on BACnet values (DESIGN.md 6.5). Written to ``fuxa/<variant>/<host>-project.json``;
  the cherrymountain app builds the same project at pre-start.

Tag IDs come from plantgen, so they stay stable across regenerations; device,
view, and gauge IDs are derived from names with UUIDv5 for the same reason. The
project schema follows frangoteam/FUXA ``client/src/app/_models`` and
``server/runtime/project`` (see README.md for what is unverified).

Usage:
    python build_project.py [--variant ci] [--host scada-fuxa] [--out FILE]
    python build_project.py --check            # the ci project is current
    python build_project.py --all [--check]    # every variant and FUXA host
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

HERE: Path = Path(__file__).resolve().parent
PLANT_GENERATED: Path = HERE.parents[0] / "plant" / "generated"
NAMESPACE: uuid.UUID = uuid.uuid5(uuid.NAMESPACE_URL, "cherrymountain-brewery/fuxa")
FUXA_PROJECT_VERSION: str = "1.01"
PROJECT_NAME: str = "cherrymountain-ci"
FERMENTERS: tuple[str, ...] = ("FV101", "FV102", "FV103", "FV104")
OUTPUT_UNIT: int = 1  # PropertyType.output in the FUXA editor (unit and digits)


def uid(prefix: str, name: str) -> str:
    """Stable ID for ``name``: ``<prefix>_<uuid5>``."""
    return f"{prefix}{uuid.uuid5(NAMESPACE, name)}"


# ---------------------------------------------------------------- inputs


class PlantTag(BaseModel):
    """One tag from <host>-tags.json (fields this builder uses)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    label: str
    type: str
    memaddress: str | None = None
    address: int | str | None = None
    divisor: int | None = None
    units: str | None = None
    range: tuple[float, float] | None = None
    plc_iec: str | None = None
    access: str = "read"


class PlantDevice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    type: str
    property: dict[str, Any]
    polling: int
    tags: list[PlantTag]


class PlantTags(BaseModel):
    model_config = ConfigDict(extra="ignore")

    devices: list[PlantDevice]


class PlantAlarm(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    fuxa_severity: str
    fuxa_tag_id: str
    text: str
    kind: str = "state"
    limit: float = 1.0
    evaluator: str | None = None


class PlantAlarms(BaseModel):
    model_config = ConfigDict(extra="ignore")

    alarms: list[PlantAlarm]


# ---------------------------------------------------------------- devices


class TagDaq(BaseModel):
    enabled: bool
    changed: bool = False
    interval: int = 60
    restored: bool = False


class FuxaTag(BaseModel):
    """FUXA ``Tag`` (for Modbus, ``address`` is 1-based)."""

    id: str
    name: str
    label: str
    type: str
    memaddress: str | None = None
    address: str
    divisor: int | None = None
    format: int | None = None
    description: str | None = None
    daq: TagDaq


class FuxaDevice(BaseModel):
    id: str
    name: str
    enabled: bool = True
    property: dict[str, Any]
    type: str
    polling: int
    tags: dict[str, FuxaTag]


def is_analog(tag: PlantTag) -> bool:
    return tag.type != "Bool"


def convert_tag(tag: PlantTag) -> FuxaTag:
    # DAQ goes to FUXA's built-in SQLite (no historian in the ci variant). Only
    # process values are stored: PV tags, which are the ones that trend.
    daq: bool = tag.name.endswith(".PV") and is_analog(tag)
    return FuxaTag(
        id=tag.id,
        name=tag.name,
        label=tag.label,
        type=tag.type,
        memaddress=tag.memaddress,
        address=str(tag.address),
        divisor=tag.divisor,
        format=2 if is_analog(tag) else None,
        description=tag.label,
        daq=TagDaq(enabled=daq),
    )


def convert_device(dev: PlantDevice) -> FuxaDevice:
    props: dict[str, Any] = dict(dev.property)
    props["port"] = str(props.get("port", 502))
    props["slaveid"] = str(props.get("slaveid", 1))
    return FuxaDevice(
        id=uid("d_", dev.name),
        name=dev.name,
        property=props,
        type=dev.type,
        polling=dev.polling,
        tags={t.id: convert_tag(t) for t in dev.tags},
    )


# ---------------------------------------------------------------- view


class Gauge(BaseModel):
    """FUXA ``GaugeSettings``: one widget bound to a tag."""

    id: str
    type: str
    name: str
    property: dict[str, Any]
    label: str


class ViewBuilder:
    """Collects SVG fragments and the matching gauge settings for one view."""

    def __init__(self, tags: dict[str, PlantTag], width: int, height: int) -> None:
        self.tags: dict[str, PlantTag] = tags
        self.width: int = width
        self.height: int = height
        self.svg: list[str] = []
        self.items: dict[str, Gauge] = {}

    def text(self, x: int, y: int, content: str, size: int = 13, bold: bool = False,
             anchor: str = "start") -> None:
        weight: str = ' font-weight="bold"' if bold else ""
        self.svg.append(
            f'<text xml:space="preserve" x="{x}" y="{y}" font-size="{size}"'
            f' font-family="sans-serif" text-anchor="{anchor}" fill="#212121"'
            f' stroke-width="0"{weight}>{content}</text>'
        )

    def line(self, x1: int, y1: int, x2: int, y2: int) -> None:
        self.svg.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#9e9e9e" stroke-width="1"/>'
        )

    def _tag(self, name: str) -> PlantTag:
        return self.tags[name]

    def value(self, x: int, y: int, tag_name: str, digits: int, unit: str) -> None:
        tag: PlantTag = self._tag(tag_name)
        gid: str = uid("VAL_", tag_name)
        self.svg.append(
            f'<g id="{gid}" type="svg-ext-value" fill="#000000" stroke="#000000"'
            f' font-size="14" stroke-width="0" font-family="sans-serif" text-anchor="middle">'
            f'<text stroke-width="0" id="{uid("VAL_", tag_name + "/text")}" fill="#000000"'
            f' stroke="#000000" font-size="14" font-family="sans-serif" text-anchor="middle"'
            f' xml:space="preserve" x="{x}" y="{y}">##.##</text></g>'
        )
        self.items[gid] = Gauge(
            id=gid,
            type="svg-ext-value",
            name=tag_name,
            label="Value",
            property={
                "events": [],
                "variableId": tag.id,
                "ranges": [
                    {"type": OUTPUT_UNIT, "min": 0, "max": 0, "text": unit,
                     "textId": "", "fractionDigits": digits}
                ],
            },
        )

    def setpoint_input(self, x: int, y: int, tag_name: str, lo: float, hi: float) -> None:
        tag: PlantTag = self._tag(tag_name)
        gid: str = uid("HXI_", tag_name)
        w, h = 64, 24
        self.svg.append(
            f'<g id="{gid}" stroke="#000000" text-anchor="right" font-family="sans-serif"'
            f' font-size="14" fill="#f1f1f1ff" type="svg-ext-html_input">'
            f'<rect id="{uid("svg_", tag_name + "/rect")}" stroke="null" fill="#ffffff"'
            f' height="{h}" width="{w}" y="{y}" x="{x}" stroke-width="0"/>'
            f'<foreignObject id="H-{uid("HXI_", tag_name + "/fo")}" width="{w}" height="{h}"'
            f' y="{y}" x="{x}"><INPUT id="I-{uid("HXI_", tag_name + "/in")}"'
            f' style="width: calc(100% - 7px); height: calc(100% - 7px); text-align: right;'
            f' border: unset; background-color: rgb(255, 255, 255); color: rgb(0, 0, 0);'
            f' vector-effect: non-scaling-stroke;" type="text"/></foreignObject></g>'
        )
        self.items[gid] = Gauge(
            id=gid,
            type="svg-ext-html_input",
            name=tag_name,
            label="HtmlInput",
            property={
                "events": [],
                "variableId": tag.id,
                "ranges": [
                    {"type": OUTPUT_UNIT, "min": 0, "max": 0, "text": "",
                     "textId": "", "fractionDigits": 1}
                ],
                "options": {"updated": True, "numeric": True, "min": lo, "max": hi,
                            "type": "number", "selectOnClick": True},
            },
        )

    def lamp(self, cx: int, cy: int, tag_name: str, on_color: str) -> None:
        tag: PlantTag = self._tag(tag_name)
        gid: str = uid("GSE_", tag_name)
        self.svg.append(
            f'<g id="{gid}" type="svg-ext-gauge_semaphore" stroke="#616161" stroke-width="1">'
            f'<circle id="{uid("GSE_", tag_name + "/c")}" cx="{cx}" cy="{cy}" r="8"'
            f' fill="#bdbdbd" stroke="#616161"/></g>'
        )
        self.items[gid] = Gauge(
            id=gid,
            type="svg-ext-gauge_semaphore",
            name=tag_name,
            label="HtmlSemaphore",
            property={
                "events": [],
                "variableId": tag.id,
                "ranges": [
                    {"min": 0, "max": 0, "color": "#bdbdbd", "stroke": "#616161"},
                    {"min": 1, "max": 1, "color": on_color, "stroke": "#212121"},
                ],
            },
        )

    def svg_content(self) -> str:
        body: str = "\n  ".join(self.svg)
        return (
            f'<svg width="{self.width}" height="{self.height}"'
            f' xmlns="http://www.w3.org/2000/svg" xmlns:svg="http://www.w3.org/2000/svg">'
            f"\n <g>\n  <title>Layer 1</title>\n  {body}\n </g>\n</svg>"
        )


COLUMN_WIDTH: int = 290
LEFT: int = 20
ALARM_LAMPS: tuple[tuple[str, str], ...] = (
    ("TT01.ALM_DEVIATION", "Side dev"),
    ("TT01.ALM_HIGH", "Side hi"),
    ("TT02.ALM_DEVIATION", "Cone dev"),
    ("PT01.ALM_HIGH", "Press hi"),
    ("PT01.ALM_LOW", "Press lo"),
)


def build_cellar_view(tags: dict[str, PlantTag]) -> dict[str, Any]:
    """The Cellar view: one column per fermenter."""
    width: int = LEFT * 2 + COLUMN_WIDTH * len(FERMENTERS)
    vb: ViewBuilder = ViewBuilder(tags, width=width, height=470)
    vb.text(LEFT, 28, "Cellar: fermenters FV-101 to FV-104", size=18, bold=True)
    vb.text(width - LEFT, 28, "Sim hours", size=12, anchor="end")
    vb.value(width - LEFT - 100, 28, "CEL.CLK.KI01.PV", digits=1, unit="h")

    for i, fv in enumerate(FERMENTERS):
        x0: int = LEFT + i * COLUMN_WIDTH
        label_x: int = x0
        side_x: int = x0 + 150
        cone_x: int = x0 + 225
        if i:
            vb.line(x0 - 8, 50, x0 - 8, 450)
        vb.text(x0, 62, f"FV-{fv[2:]}", size=16, bold=True)
        vb.text(side_x, 62, "Side", size=12, anchor="middle")
        vb.text(cone_x, 62, "Cone", size=12, anchor="middle")

        vb.text(label_x, 96, "Temperature")
        vb.value(side_x, 96, f"CEL.{fv}.TT01.PV", digits=1, unit="F")
        vb.value(cone_x, 96, f"CEL.{fv}.TT02.PV", digits=1, unit="F")

        vb.text(label_x, 136, "Setpoint (F)")
        vb.setpoint_input(side_x - 32, 118, f"CEL.{fv}.TT01.SP", 20, 90)
        vb.setpoint_input(cone_x - 32, 118, f"CEL.{fv}.TT02.SP", 20, 90)

        vb.text(label_x, 176, "Glycol valve")
        vb.lamp(side_x, 171, f"CEL.{fv}.TV01.CMD", "#1e88e5")
        vb.lamp(cone_x, 171, f"CEL.{fv}.TV02.CMD", "#1e88e5")

        vb.text(label_x, 226, "Head pressure")
        vb.value(side_x, 226, f"CEL.{fv}.PT01.PV", digits=2, unit="psia")
        vb.text(label_x, 256, "Spund setpoint")
        vb.value(side_x, 256, f"CEL.{fv}.PCV01.SP", digits=2, unit="psia")
        vb.text(label_x, 286, "Spund valve open")
        vb.lamp(side_x, 281, f"CEL.{fv}.ZSO01.PV", "#fb8c00")
        vb.text(label_x, 316, "Extract")
        vb.value(side_x, 316, f"CEL.{fv}.DT01.PV", digits=2, unit="P")

        vb.text(label_x, 356, "Alarms", size=12, bold=True)
        for j, (suffix, caption) in enumerate(ALARM_LAMPS):
            lx: int = x0 + 10 + j * 54
            vb.lamp(lx, 380, f"CEL.{fv}.{suffix}", "#e53935")
            vb.text(lx, 408, caption, size=9, anchor="middle")

    view_id: str = uid("v_", "Cellar")
    return {
        "id": view_id,
        "name": "Cellar",
        "profile": {
            "width": width,
            "height": 470,
            "bkcolor": "#f5f5f5ff",
            "margin": 10,
            "align": "topCenter",
            "gridType": "fixed",
            "viewRenderDelay": 0,
        },
        "items": {k: g.model_dump() for k, g in vb.items.items()},
        "variables": {},
        "svgcontent": vb.svg_content(),
        "type": "svg",
    }


def build_layout(view_id: str) -> dict[str, Any]:
    """Navigation: the Cellar view is the start view; alarms show in the header."""
    return {
        "start": view_id,
        "navigation": {
            "mode": "fix",
            "type": "inline",
            "bkcolor": "#F4F5F7",
            "fgcolor": "#1D1D1D",
            "items": [{"icon": "dashboard", "view": view_id, "link": "", "text": "Cellar"}],
        },
        "header": {
            "title": "CherryMountain Brewery",
            "alarms": "fix",
            "infos": "hide",
            "bkcolor": "#ffffff",
            "fgcolor": "#000000",
            "items": [],
        },
        "showdev": False,
        "zoom": "enabled",
    }


# ---------------------------------------------------------------- alarms

ACK_MODE: dict[str, str] = {"high": "ackactive", "low": "ackactive", "info": "float"}
COLORS: dict[str, tuple[str, str]] = {
    "high": ("#FF4848", "#FFFFFF"),
    "low": ("#F9CF59", "#000000"),
    "info": ("#66CCFF", "#000000"),
}


def sub_property(severity: str, level: str, text: str, group: str) -> dict[str, Any]:
    """One FUXA alarm level (``highhigh``, ``high``, ``low``, ``info``)."""
    on: bool = level == severity
    bk, fg = COLORS.get(level, ("#FFFFFF", "#000000"))
    return {
        "enabled": on,
        "checkdelay": 1,
        "min": 1 if on else None,
        "max": 1 if on else None,
        "timedelay": 0,
        "text": text if on else "",
        "group": group if on else "",
        "ackmode": ACK_MODE.get(level, "float"),
        "bkcolor": bk,
        "color": fg,
    }


def build_alarm(alarm: PlantAlarm) -> dict[str, Any]:
    """A FUXA alarm on a PLC alarm bit: active while the bit is 1.

    The PLC applies the alarm on-delay (alarms.json ``delay_s``), so FUXA's own
    delay is 0 and it only samples the bit once a second.
    """
    unit: str = alarm.name.split(".")[1]  # FV101
    group: str = f"FV-{unit[2:]}"
    return {
        "name": alarm.name,
        "property": {"variableId": alarm.fuxa_tag_id},
        "highhigh": sub_property(alarm.fuxa_severity, "highhigh", alarm.text, group),
        "high": sub_property(alarm.fuxa_severity, "high", alarm.text, group),
        "low": sub_property(alarm.fuxa_severity, "low", alarm.text, group),
        "info": sub_property(alarm.fuxa_severity, "info", alarm.text, group),
        "actions": {"enabled": False, "values": []},
        "value": "",
    }


# ---------------------------------------------------------------- project


class Project(BaseModel):
    """FUXA ``ProjectData`` (only the sections this project fills)."""

    version: str = FUXA_PROJECT_VERSION
    name: str = PROJECT_NAME
    server: dict[str, Any] = Field(
        default_factory=lambda: {
            "id": "0", "name": "FUXA Server", "type": "FuxaServer", "property": {}
        }
    )
    hmi: dict[str, Any]
    devices: dict[str, FuxaDevice]
    alarms: list[dict[str, Any]]
    charts: list[Any] = Field(default_factory=list)
    graphs: list[Any] = Field(default_factory=list)
    recipes: list[Any] = Field(default_factory=list)
    notifications: list[Any] = Field(default_factory=list)
    scripts: list[Any] = Field(default_factory=list)
    reports: list[Any] = Field(default_factory=list)
    texts: list[Any] = Field(default_factory=list)
    plugin: list[Any] = Field(default_factory=list)
    mapsLocations: list[Any] = Field(default_factory=list)


# ---------------------------------------------------------------- full and lite

#: The S7 data blocks the cherrymountain app gives the ``s7comm`` plugin
#: (render.s7comm_config): IEC area -> (DB number, bit image).
S7_DATA_BLOCKS: dict[str, tuple[int, bool]] = {
    "QW": (1, False),
    "IW": (2, False),
    "MW": (3, False),
    "IX": (4, True),
    "QX": (5, True),
}
#: The OPC UA namespace index of the plugin's ``urn:cherrymountain:opcua``
#: (the first one it registers). TODO(verify): on a running plc-brewhouse.
OPCUA_NAMESPACE: int = 2
FUXA_HOSTS: tuple[str, ...] = ("scada-fuxa", "bas-fuxa")
VARIANTS: tuple[str, ...] = ("full", "lite")
#: Gauges per generated device view: columns of label and value.
VIEW_ROWS: int = 24
VIEW_COLUMN: int = 360


def s7_address(plc_iec: str) -> tuple[str, str]:
    """(FUXA S7 address, type) of a located variable: DB<n>.DBW<byte> or
    DB<n>.DBX<byte>.<bit>, in the data blocks of S7_DATA_BLOCKS."""
    area: str = plc_iec[1:3]
    if area not in S7_DATA_BLOCKS:
        raise SystemExit(f"{plc_iec}: no S7 data block for %{area}")
    db, bits = S7_DATA_BLOCKS[area]
    if bits:
        byte, _, bit = plc_iec[3:].partition(".")
        return f"DB{db}.DBX{int(byte)}.{int(bit)}", "Bool"
    return f"DB{db}.DBW{int(plc_iec[3:]) * 2}", "Word"


def convert_plant_tag(dev: PlantDevice, tag: PlantTag) -> FuxaTag:
    """A FUXA tag for any device type of the full and lite tag lists."""
    daq: bool = tag.name.endswith(".PV") and is_analog(tag)
    tag_type: str = tag.type
    memaddress: str | None = tag.memaddress
    match dev.type:
        case "ModbusTCP":
            address: str = str(tag.address)
        case "OPCUA":
            address = f"ns={OPCUA_NAMESPACE};s={tag.name}"
            tag_type = "Boolean" if tag.type == "Bool" else tag.type
        case "SiemensS7":
            if tag.plc_iec is None:
                raise SystemExit(f"{tag.name}: an S7 tag needs plc_iec")
            address, tag_type = s7_address(tag.plc_iec)
            if tag.type == "Int16":
                tag_type = "Int"
        case "BACnet":
            address = str(tag.address)
        case _:
            raise SystemExit(f"{dev.name}: unsupported device type {dev.type}")
    return FuxaTag(
        id=tag.id,
        name=tag.name,
        label=tag.label,
        type=tag_type,
        memaddress=memaddress,
        address=address,
        divisor=tag.divisor,
        format=2 if is_analog(tag) else None,
        description=tag.label,
        daq=TagDaq(enabled=daq),
    )


def convert_plant_device(dev: PlantDevice) -> FuxaDevice:
    props: dict[str, Any] = dict(dev.property)
    if dev.type == "ModbusTCP":
        props["port"] = str(props.get("port", 502))
        props["slaveid"] = str(props.get("slaveid", 1))
    return FuxaDevice(
        id=uid("d_", dev.name),
        name=dev.name,
        property=props,
        type=dev.type,
        polling=dev.polling,
        tags={t.id: convert_plant_tag(dev, t) for t in dev.tags},
    )


def view_name(device: str) -> str:
    """plc-brewhouse -> Brewhouse; bas-fuxa-bacnet -> Building."""
    if device.endswith("-bacnet"):
        return "Building" if device.startswith("bas-") else "Cellar CO2"
    return device.removeprefix("plc-").capitalize()


def build_device_view(
    dev: PlantDevice, tags: dict[str, PlantTag], taken: frozenset[str] = frozenset()
) -> dict[str, Any]:
    """A generated view of one device: its process values, setpoints, and states
    in columns (label, then value or lamp), read-only. FUXA opens a view by its
    name (``/home/<name>``, which the kiosks use), so a name in ``taken`` gets
    " points" appended: plc-cellar's view is "Cellar points" next to the
    hand-laid "Cellar" view."""
    shown: list[PlantTag] = [t for t in dev.tags if ".ALM_" not in t.name]
    columns: int = max(1, -(-len(shown) // VIEW_ROWS))
    width: int = LEFT * 2 + VIEW_COLUMN * columns
    height: int = 70 + 26 * min(len(shown), VIEW_ROWS)
    title: str = view_name(dev.name)
    if title in taken:
        title = f"{title} points"
    vb: ViewBuilder = ViewBuilder(tags, width=width, height=height)
    vb.text(LEFT, 28, f"{title}: {dev.name}", size=18, bold=True)
    for i, tag in enumerate(shown):
        x0: int = LEFT + (i // VIEW_ROWS) * VIEW_COLUMN
        y: int = 64 + 26 * (i % VIEW_ROWS)
        vb.text(x0, y, textwrap.shorten(tag.label, 36, placeholder=" ..."), size=12)
        if is_analog(tag):
            digits: int = 0 if not tag.divisor or tag.divisor <= 1 else len(str(tag.divisor)) - 1
            vb.value(x0 + 300, y, tag.name, digits=digits, unit=tag.units or "")
        else:
            vb.lamp(x0 + 300, y - 5, tag.name, "#1e88e5")
    view_id: str = uid("v_", f"{dev.name}/view")
    return {
        "id": view_id,
        "name": title,
        "profile": {
            "width": width,
            "height": height,
            "bkcolor": "#f5f5f5ff",
            "margin": 10,
            "align": "topCenter",
            "gridType": "fixed",
            "viewRenderDelay": 0,
        },
        "items": {k: g.model_dump() for k, g in vb.items.items()},
        "variables": {},
        "svgcontent": vb.svg_content(),
        "type": "svg",
    }


def build_plant_layout(views: list[dict[str, Any]]) -> dict[str, Any]:
    layout: dict[str, Any] = build_layout(views[0]["id"])
    layout["navigation"]["items"] = [
        {"icon": "dashboard", "view": v["id"], "link": "", "text": v["name"]} for v in views
    ]
    return layout


FAR: float = 1.0e9


def build_plant_alarm(alarm: PlantAlarm) -> dict[str, Any]:
    """A PLC alarm bit (active at 1), or an alarm FUXA evaluates on a value:
    high and highhigh from the limit up, low and lowlow from it down, state at
    the limit. FUXA applies no delay; the PLC alarm bits carry their own."""
    group: str = ".".join(alarm.name.split(".")[:2])
    built: dict[str, Any] = build_alarm(alarm)
    for level in ("highhigh", "high", "low", "info"):
        built[level] = sub_property(alarm.fuxa_severity, level, alarm.text, group)
    if alarm.evaluator == "fuxa":
        sub: dict[str, Any] = built[alarm.fuxa_severity]
        match alarm.kind:
            case "high" | "highhigh":
                sub["min"], sub["max"] = alarm.limit, FAR
            case "low" | "lowlow":
                sub["min"], sub["max"] = -FAR, alarm.limit
            case _:
                sub["min"], sub["max"] = alarm.limit, alarm.limit
    return built


def build_plant_project(variant: str, host: str) -> Project:
    gen: Path = PLANT_GENERATED / variant
    path: Path = gen / "fuxa" / f"{host}-tags.json"
    if not path.is_file():
        raise SystemExit(f"no tag list for {host} in variant {variant}")
    plant: PlantTags = PlantTags.model_validate_json(path.read_text(encoding="utf-8"))
    alarms: PlantAlarms = PlantAlarms.model_validate_json((gen / "alarms.json").read_text(encoding="utf-8"))
    tags: dict[str, PlantTag] = {t.name: t for d in plant.devices for t in d.tags}
    ids: set[str] = {t.id for t in tags.values()}
    views: list[dict[str, Any]] = []
    if all(f"CEL.{fv}.TT01.PV" in tags for fv in FERMENTERS):
        views.append(build_cellar_view(tags))
    hand_laid: frozenset[str] = frozenset(v["name"] for v in views)
    views += [build_device_view(d, tags, hand_laid) for d in plant.devices]
    names: list[str] = [v["name"] for v in views]
    if len(set(names)) != len(names):
        # FUXA finds a view by its name, so a second one of the same name
        # cannot be opened by URL.
        raise SystemExit(f"two views of {host} share a name: {sorted(names)}")
    devices: list[FuxaDevice] = [convert_plant_device(d) for d in plant.devices]
    return Project(
        name=f"cherrymountain-{variant}-{host}",
        hmi={"layout": build_plant_layout(views), "views": views},
        devices={d.id: d for d in devices},
        alarms=[build_plant_alarm(a) for a in alarms.alarms if a.fuxa_tag_id in ids],
    )


def build_project(variant: str, host: str = "scada-fuxa") -> Project:
    if variant != "ci":
        return build_plant_project(variant, host)
    if host != "scada-fuxa":
        raise SystemExit("the ci variant has only scada-fuxa")
    gen: Path = PLANT_GENERATED / variant
    plant: PlantTags = PlantTags.model_validate_json(
        (gen / "fuxa" / "scada-fuxa-tags.json").read_text(encoding="utf-8")
    )
    alarms: PlantAlarms = PlantAlarms.model_validate_json((gen / "alarms.json").read_text(encoding="utf-8"))
    if len(plant.devices) != 1 or plant.devices[0].name != "plc-cellar":
        raise SystemExit("expected exactly one device, plc-cellar, in the tag list")

    by_name: dict[str, PlantTag] = {t.name: t for t in plant.devices[0].tags}
    by_id: dict[str, PlantTag] = {t.id: t for t in plant.devices[0].tags}
    missing: list[str] = [a.name for a in alarms.alarms if a.fuxa_tag_id not in by_id]
    if missing:
        raise SystemExit(f"alarm tags missing from the tag list: {missing}")

    view: dict[str, Any] = build_cellar_view(by_name)
    device: FuxaDevice = convert_device(plant.devices[0])
    return Project(
        hmi={"layout": build_layout(view["id"]), "views": [view]},
        devices={device.id: device},
        alarms=[build_alarm(a) for a in alarms.alarms],
    )


def render(project: Project) -> str:
    data: dict[str, Any] = project.model_dump(exclude_none=True)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def output(variant: str, host: str) -> Path:
    if variant == "ci":
        return HERE / "scada-fuxa-project.json"
    return HERE / variant / f"{host}-project.json"


def write_or_check(variant: str, host: str, out: Path, check: bool) -> bool:
    text: str = render(build_project(variant, host))
    if check:
        if not out.exists() or out.read_text(encoding="utf-8") != text:
            print(f"{out} is stale; run build_project.py", file=sys.stderr)
            return False
        print(f"{out} is up to date")
        return True
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}")
    return True


def main(argv: list[str] | None = None) -> int:
    ap: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--variant", default="ci")
    ap.add_argument("--host", default="scada-fuxa", choices=FUXA_HOSTS)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--check", action="store_true", help="fail if the output is stale")
    ap.add_argument("--all", action="store_true", help="every variant and FUXA host")
    args: argparse.Namespace = ap.parse_args(argv)
    if args.all:
        jobs: list[tuple[str, str]] = [("ci", "scada-fuxa")] + [(v, h) for v in VARIANTS for h in FUXA_HOSTS]
        ok: bool = all([write_or_check(v, h, output(v, h), args.check) for v, h in jobs])
        return 0 if ok else 1
    out: Path = args.out or output(args.variant, args.host)
    return 0 if write_or_check(args.variant, args.host, out, args.check) else 1


if __name__ == "__main__":
    sys.exit(main())
