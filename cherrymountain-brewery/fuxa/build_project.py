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
import math
import sys
import textwrap
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

HERE: Path = Path(__file__).resolve().parent
# The app and the tests load this file by its path, so its directory is not on
# the import path: put it there for the modules beside it.
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import hmi_views  # noqa: E402
from hmi_base import (  # noqa: E402
    BRAND,
    BRAND_TOKENS,
    NAMESPACE,
    OUTPUT_UNIT,
    PLANT_GENERATED,
    Brand,
    Gauge,
    PlantAlarm,
    PlantAlarms,
    PlantDevice,
    PlantTag,
    PlantTags,
    ViewBuilder,
    load_brand,
    uid,
)

__all__ = [
    "BRAND", "BRAND_TOKENS", "NAMESPACE", "OUTPUT_UNIT", "PLANT_GENERATED", "Brand", "Gauge",
    "PlantAlarm", "PlantAlarms", "PlantDevice", "PlantTag", "PlantTags", "ViewBuilder",
    "build_project", "load_brand", "main", "render", "uid",
]

FUXA_PROJECT_VERSION: str = "1.01"
PROJECT_NAME: str = "cherrymountain-ci"
FERMENTERS: tuple[str, ...] = ("FV101", "FV102", "FV103", "FV104")


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
    scale: dict[str, Any] | None = None
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



LEFT: int = 20


#: The brewery's name as it is written on every screen.
BRAND_NAME: str = "CherryMountain Brewery"
#: Where the mark is served from (FUXA serves ``<server>/_images`` at ``/_images``, no sign-in
#: needed): the cherrymountain app and the ci topology inject ``brand/icon.svg`` there.
LOGO_URL: str = "_images/Brand/icon.svg"
#: The header's height in px (FUXA's default is 46): room for the mark at 48 px in the menu
#: above it, and a 3 px stripe at the bottom.
HEADER_HEIGHT: int = 56
STRIPE_HEIGHT: int = 3
#: The mark's size in px: 48 is where the full drawing starts to hold up (brand/README.md).
LOGO_SIZE: int = 48


#: The inline color FUXA gives the header's account icon once someone is signed in
#: (``home.component.html``); the CSS recolors exactly that state.
FUXA_SIGNED_IN_BLUE: str = "59, 144, 255"


def frame_css(brand: Brand = BRAND) -> str:
    """The CSS FUXA adds to every page (``layout.customStyles``, applied by
    ``app.component.ts`` ``applyCustomCss``): the ember stripe under the header, the
    signed-in account icon, the mark's row at the top of the menu, and the sign-in
    dialog. It only reaches the frame: every rule names the header, the menu, or the
    sign-in dialog, never a view or a view's buttons."""
    ink, coal, ash = brand.color("ink"), brand.color("coal"), brand.color("ash")
    paper, flame = brand.role("header_fg"), brand.role("link")
    stripe, primary, primary_fg = brand.role("stripe"), brand.role("primary"), brand.role("primary_fg")
    return textwrap.dedent(
        f"""\
        /* The frame of {BRAND_NAME}: header, menu, and sign-in dialog. Colors are the brand tokens. */
        .header {{
          background-image: linear-gradient({stripe}, {stripe});
          background-size: 100% {STRIPE_HEIGHT}px;
          background-repeat: no-repeat;
          background-position: left bottom;
        }}
        .header .header-items-button button {{ pointer-events: none; }}
        .header .header-items-button .mdc-button__label {{ font-weight: 700; }}
        .header .header-login mat-icon[style*="{FUXA_SIGNED_IN_BLUE}"] {{ color: {flame} !important; }}
        .sidenav-menu .sidenav-header {{ height: {HEADER_HEIGHT}px !important; padding-bottom: 0 !important; }}
        .sidenav-menu .sidenav-header img {{ width: {LOGO_SIZE}px; height: {LOGO_SIZE}px; }}
        .mat-mdc-dialog-container:has(app-login) {{
          --mdc-dialog-container-color: {coal};
          --mdc-dialog-subhead-color: {paper};
          --mdc-dialog-supporting-text-color: {paper};
        }}
        .mat-mdc-dialog-container:has(app-login) .mdc-dialog__surface {{ border-top: {STRIPE_HEIGHT}px solid {stripe}; }}
        app-login h1.mat-mdc-dialog-title::before {{
          content: "";
          display: block;
          width: {LOGO_SIZE}px;
          height: {LOGO_SIZE}px;
          margin: 14px 0 10px;
          background: url({LOGO_URL}) center / contain no-repeat;
        }}
        app-login .my-form-field span {{ color: {paper}; }}
        app-login .my-form-field input {{ background-color: {ink}; color: {paper}; }}
        .mat-mdc-dialog-container app-login .mat-mdc-raised-button.mat-primary {{
          --mdc-protected-button-container-color: {primary};
          --mdc-protected-button-label-text-color: {primary_fg};
        }}
        .mat-mdc-dialog-container app-login .mat-mdc-raised-button.mat-unthemed {{
          --mdc-protected-button-container-color: {ash};
          --mdc-protected-button-label-text-color: {paper};
        }}
        """
    )


def build_layout(view_id: str) -> dict[str, Any]:
    """Navigation and header: the Cellar view is the start view; alarms show in the
    header. The header is ink with paper text and an ember stripe, the menu coal with
    paper text and the mark at its top, and the sign-in dialog is themed by the CSS."""
    return {
        "start": view_id,
        "navigation": {
            "mode": "fix",
            "type": "inline",
            "bkcolor": BRAND.role("menu_bg"),
            "fgcolor": BRAND.role("menu_fg"),
            "logo": LOGO_URL,
            "items": [{"icon": "dashboard", "view": view_id, "link": "", "text": "Cellar"}],
        },
        "header": {
            "title": BRAND_NAME,
            "alarms": "fix",
            "infos": "hide",
            "bkcolor": BRAND.role("header_bg"),
            "fgcolor": BRAND.role("header_fg"),
            "height": HEADER_HEIGHT,
            "fontSize": 18,
            "loginInfo": "username",
            "items": [
                {
                    "id": uid("i_", "brand-name"),
                    "type": "label",
                    "marginLeft": 5,
                    "marginRight": 5,
                    "property": {"text": BRAND_NAME, "events": [], "actions": []},
                }
            ],
        },
        "showdev": False,
        # FUXA 1.3.6 reads this without a default while it sets up an input box
        # and stops setting the box up when it is missing.
        "inputdialog": "false",
        "zoom": "enabled",
        "customStyles": frame_css(),
    }


# ---------------------------------------------------------------- alarms

#: Per FUXA alarm level (plantgen maps critical, high, medium, and low priority
#: to highhigh, high, low, and info): every level but the lowest stays listed
#: until an operator acknowledges it, the critical one in the darkest red.
ACK_MODE: dict[str, str] = {
    "highhigh": "ackactive",
    "high": "ackactive",
    "low": "ackactive",
    "info": "float",
}
COLORS: dict[str, tuple[str, str]] = {
    "highhigh": (BRAND.alarm("critical"), "#FFFFFF"),
    "high": (BRAND.alarm("high"), "#FFFFFF"),
    "low": (BRAND.alarm("medium"), "#000000"),
    "info": (BRAND.alarm("low"), "#000000"),
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


class ServerTag(BaseModel):
    """A tag of FUXA's own ``FuxaServer`` device (``type`` ``number`` or
    ``string``, set by the batch engine script or the Batch view)."""

    id: str
    name: str
    label: str
    type: Literal["number", "string"]
    init: str
    description: str
    daq: TagDaq


class ServerDevice(BaseModel):
    """The ``FuxaServer`` device, with the batch engine's tags."""

    id: str
    name: str
    enabled: bool
    type: Literal["FuxaServer"]
    property: dict[str, Any]
    polling: int
    tags: dict[str, ServerTag]


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
    devices: dict[str, FuxaDevice | ServerDevice]
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
#: Gauges per generated device view: columns of label, value, and (for a tag
#: the operator may write) an input box. An HMI station's screen is 1440x900
#: and the menu takes 200 px of it, so a view is at most three columns across
#: (1,240 px) and thirty rows down. A device with more tags than that goes on
#: in a second band of columns below the first, which the page scrolls to. A
#: wider view was cut off at the screen's edge: the conveyors, the fourth
#: column of the Packaging view, could not be seen at the packaging station.
VIEW_ROWS: int = 30
VIEW_ROW_HEIGHT: int = 25
VIEW_COLUMNS: int = 3
VIEW_COLUMN: int = 400
#: Space between two bands of columns.
VIEW_BAND_GAP: int = 30
#: Offsets in a column: where a value ends (it is right-aligned, so a long unit
#: grows toward the label), a lamp's center, and the input box.
VIEW_VALUE_X: int = 316
VIEW_LAMP_X: int = 300
VIEW_INPUT_X: int = 326
#: Longest label, in characters, beside a value with no unit or a lamp.
VIEW_LABEL_CHARS: int = 42


def view_label(tag: PlantTag) -> str:
    """A tag's label, cut to what fits in front of its value: a value with a long
    unit ("30.0 bottles/min") reaches further left than a bare number. The cut
    is between words (a word cut short reads as a misspelling)."""
    room: int = VIEW_LABEL_CHARS
    if is_analog(tag) and tag.units:
        # 12 px label text is about 6.2 px a character, 14 px value text about 7.5
        room = min(room, int((VIEW_VALUE_X - 8 - 7.5 * (7 + len(tag.units))) / 6.2))
    return textwrap.shorten(tag.label, room, placeholder=" ...")


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


def linear_scale(divisor: int | None) -> dict[str, Any] | None:
    """FUXA's linear tag scale for a raw register of ``value x divisor``.

    FUXA applies ``divisor`` in its Modbus driver only (``Tag.divisor``:
    "used by Modbus"); every driver applies ``scale`` on reads and inverts it
    on writes, so OPC UA and S7 tags carry the factor this way.
    """
    if not divisor or divisor == 1:
        return None
    return {"mode": "linear", "rawLow": 0, "rawHigh": divisor, "scaledLow": 0, "scaledHigh": 1}


def convert_plant_tag(dev: PlantDevice, tag: PlantTag, trended: frozenset[str] = frozenset()) -> FuxaTag:
    """A FUXA tag for any device type of the full and lite tag lists. FUXA stores
    the analog process values, and any other tag a trend draws (``trended``, by id)."""
    daq: bool = (tag.name.endswith(".PV") and is_analog(tag)) or tag.id in trended
    tag_type: str = tag.type
    memaddress: str | None = tag.memaddress
    by_driver: bool = dev.type == "ModbusTCP"
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
        divisor=tag.divisor if by_driver else None,
        scale=None if by_driver else linear_scale(tag.divisor),
        format=2 if is_analog(tag) else None,
        description=tag.label,
        daq=TagDaq(enabled=daq),
    )


def convert_plant_device(dev: PlantDevice, trended: frozenset[str] = frozenset()) -> FuxaDevice:
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
        tags={t.id: convert_plant_tag(dev, t, trended) for t in dev.tags},
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
    in columns (label, then value or lamp). A tag the operator may write
    (``access: read-write``: setpoints, commands, entry permits, resets) also
    gets an input box: a number in the tag's range, or 1 and 0 for a bit. FUXA
    opens a view by its name (``/home?viewName=<name>``, which the kiosks use),
    so a name in ``taken`` gets " points" appended: plc-cellar's view is
    "Cellar points" next to the hand-laid "Cellar" view."""
    shown: list[PlantTag] = [t for t in dev.tags if ".ALM_" not in t.name]
    columns: int = max(1, -(-len(shown) // VIEW_ROWS))
    bands: int = -(-columns // VIEW_COLUMNS)
    band_height: int = VIEW_ROWS * VIEW_ROW_HEIGHT + VIEW_BAND_GAP
    # the last band is as tall as its first column, the fullest one in it
    last_rows: int = min(len(shown) - (bands - 1) * VIEW_COLUMNS * VIEW_ROWS, VIEW_ROWS)
    width: int = LEFT * 2 + VIEW_COLUMN * min(columns, VIEW_COLUMNS)
    height: int = 70 + (bands - 1) * band_height + VIEW_ROW_HEIGHT * last_rows
    title: str = view_name(dev.name)
    if title in taken:
        title = f"{title} points"
    vb: ViewBuilder = ViewBuilder(tags, width=width, height=height)
    vb.text(LEFT, 28, f"{title}: {dev.name}", size=18, bold=True)
    for i, tag in enumerate(shown):
        column: int = i // VIEW_ROWS
        x0: int = LEFT + (column % VIEW_COLUMNS) * VIEW_COLUMN
        y: int = 64 + (column // VIEW_COLUMNS) * band_height + VIEW_ROW_HEIGHT * (i % VIEW_ROWS)
        vb.text(x0, y, view_label(tag), size=12)
        if is_analog(tag):
            digits: int = 0 if not tag.divisor or tag.divisor <= 1 else len(str(tag.divisor)) - 1
            vb.value(x0 + VIEW_VALUE_X, y, tag.name, digits=digits, unit=tag.units or "", anchor="end")
        else:
            vb.lamp(x0 + VIEW_LAMP_X, y - 5, tag.name, "#1e88e5")
        if tag.access == "read-write":
            lo, hi = tag.range if is_analog(tag) and tag.range else (0.0, 1.0)
            vb.setpoint_input(x0 + VIEW_INPUT_X, y - 17, tag.name, lo, hi, scope=f"{dev.name}/")
    view_id: str = uid("v_", f"{dev.name}/view")
    return {
        "id": view_id,
        "name": title,
        "profile": {
            "width": width,
            "height": height,
            "bkcolor": BRAND.process_bkcolor(),
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


#: The menu's icon for a view (Material icon names): a picture for each process
#: graphic, a list for the generated tables.
NAV_ICONS: dict[str, str] = {
    "Overview": "dashboard",
    "Brewhouse": "whatshot",
    "Batch": "playlist_play",
    "Cellar": "ac_unit",
    "Packaging": "local_shipping",
    "Utilities": "build",
    "Power": "flash_on",
    "Cellar CO2": "warning",
    "Building": "business",
}


def nav_icon(view: str) -> str:
    return NAV_ICONS.get(view, "list")


def build_plant_layout(views: list[dict[str, Any]]) -> dict[str, Any]:
    layout: dict[str, Any] = build_layout(views[0]["id"])
    layout["navigation"]["items"] = [
        {"icon": nav_icon(v["name"]), "view": v["id"], "link": "", "text": v["name"]} for v in views
    ]
    return layout


FAR: float = 1.0e9


def build_plant_alarm(alarm: PlantAlarm) -> dict[str, Any]:
    """A PLC alarm bit (active at 1), or an alarm FUXA evaluates on a value:
    high and highhigh from the limit up, low and lowlow from it down, state at
    the limit. A PLC alarm bit carries its own on-delay, so FUXA adds none; an
    alarm FUXA evaluates has no PLC behind it, so FUXA applies the rationalized
    on-delay (``timedelay``, in seconds). FUXA has no deadband."""
    group: str = ".".join(alarm.name.split(".")[:2])
    built: dict[str, Any] = build_alarm(alarm)
    for level in ("highhigh", "high", "low", "info"):
        built[level] = sub_property(alarm.fuxa_severity, level, alarm.text, group)
    if alarm.evaluator == "fuxa":
        sub: dict[str, Any] = built[alarm.fuxa_severity]
        sub["timedelay"] = round(alarm.delay_s)
        match alarm.kind:
            case "high" | "highhigh":
                sub["min"], sub["max"] = alarm.limit, FAR
            case "low" | "lowlow":
                sub["min"], sub["max"] = -FAR, alarm.limit
            case _:
                sub["min"], sub["max"] = alarm.limit, alarm.limit
    return built


# ---------------------------------------------------------------- batch engine

#: The batch engine script (hand-written JavaScript; its __CFG__ is the data below).
BATCH_ENGINE_JS: Path = HERE / "batch_engine.js"
#: The recipe the engine sequences (BeerJSON, DESIGN.md 3.2).
RECIPE_PATH: Path = HERE.parent / "recipes" / "fire-of-the-phenix.json"
BATCH_SCRIPT_NAME: str = "cmBatchEngine"
BATCH_RECIPE_CODE: str = "FOP"
#: The tag that tells the builder the brewhouse PLC (and so the engine) is there.
BATCH_PLC_STATE_TAG: str = "BH.BATCH.XS01.STATE"
FUXA_SERVER_ID: str = "0"
#: The most a pipe and tun lose between the HLT and the grist (K); the sim's
#: operator adds the same to the strike temperature.
STRIKE_LOSS_K: float = 1.0
#: Fermenters the engine may choose from, and the brews one holds (a 30 bbl FV
#: takes two 15 bbl brews: DESIGN.md 3.1).
FERMENTER_COUNT: int = 4


class BatchTiming(BaseModel):
    """What the engine waits for, in seconds unless noted (DESIGN.md 12.2)."""

    #: A run still going this long after it started blocks the next one.
    busy_s: int = 30
    #: After a start, wait this long for FUXA to restore the batch state.
    init_grace_s: int = 15
    #: Longest to write and read back a phase's parameters, and to wait for the
    #: PLC to answer a Start, Hold, Restart, Abort, or Reset.
    prep_s: int = 30
    handshake_s: int = 30
    #: Repeat a command the PLC did not take after this long.
    retry_s: int = 6
    #: No answer from the PLC for this long holds a running batch.
    comms_s: int = 60
    #: A start that is refused this long raises its alarm.
    refuse_grace_s: int = 10
    #: A knockout waiting this long for a fermenter raises its alarm.
    nofv_alarm_s: int = 600
    #: A fermenter reserved for a brew day stays reserved this long.
    fv_reserve_s: int = 12 * 3600
    brews_per_fv: int = 2
    #: Apparent extract below this (degP) reads as an empty fermenter.
    empty_plato: float = 0.5
    max_queue: int = 16
    #: More new panel requests than this means the counter restarted.
    max_pending: int = 64
    #: Consecutive failed runs of the script that raise its alarm.
    error_alarm: int = 3


class MashStep(BaseModel):
    name: str
    temp_f: float
    minutes: int


class BatchRecipe(BaseModel):
    """The parameters the engine takes from the BeerJSON recipe."""

    code: str
    name: str
    grain_kg: float
    mash: list[MashStep]
    boil_minutes: int
    whirlpool_minutes: int
    whirlpool_temp_f: float
    pitch_temp_f: float
    #: The setpoints, in whole degF: 168 for Palmer's 167.5.
    strike_temp_f: float
    sparge_temp_f: float


class PhaseSpec(BaseModel):
    """An ISA-88 phase of plc-brewhouse and how long to wait for it: ``base_s``
    plus ``per_unit_s`` for each unit of its parameter (kg or minutes)."""

    number: int
    name: str
    base_s: int
    per_unit_s: int = 0


PHASES: tuple[PhaseSpec, ...] = (
    # Milling takes 2 s per kg at 0.5 kg/s (50 Hz), as plc-brewhouse counts it; the
    # base is the margin.
    PhaseSpec(number=1, name="MILL", base_s=600, per_unit_s=2),
    PhaseSpec(number=2, name="HLT_HEAT", base_s=7200),
    PhaseSpec(number=3, name="MASH_IN", base_s=1800),
    PhaseSpec(number=4, name="MASH_STEP", base_s=3600, per_unit_s=60),
    PhaseSpec(number=5, name="VORLAUF", base_s=1800),
    PhaseSpec(number=6, name="LAUTER", base_s=9000),
    PhaseSpec(number=7, name="BOIL", base_s=3600, per_unit_s=60),
    PhaseSpec(number=8, name="WHIRLPOOL", base_s=900, per_unit_s=60),
    PhaseSpec(number=9, name="KNOCKOUT", base_s=3600),
    PhaseSpec(number=10, name="CIP_READY", base_s=120),
)


class SequenceStep(BaseModel):
    """One phase run of a brew: the PLC phase, its parameter, the setpoints to
    write first (degF, whole degrees: see :func:`whole_degrees`), and the time to
    wait for it."""

    n: int
    name: str
    label: str
    param: int
    hlt: int
    mlt: int
    ko: int
    timeout_s: int


class BatchTag(BaseModel):
    """A tag of the FUXA Server device that the engine, or the Batch view, uses.
    ``persist`` tags are restored after a FUXA restart; ``historian`` tags go to
    the DAQ store."""

    key: str
    name: str
    kind: Literal["number", "string"]
    label: str
    persist: bool = False
    historian: bool = False


def _batch_tags() -> tuple[BatchTag, ...]:
    def num(key: str, name: str, label: str, persist: bool = False, historian: bool = False) -> BatchTag:
        return BatchTag(key=key, name=f"BATCH.{name}", kind="number", label=label,
                        persist=persist, historian=historian)

    def text(key: str, name: str, label: str) -> BatchTag:
        return BatchTag(key=key, name=f"BATCH.{name}", kind="string", label=label)

    return (
        # The Batch view's command: 1 Start, 2 Hold, 3 Restart, 4 Abort, 5 Reset.
        num("cmd", "CMD", "Operator command (1 Start, 2 Hold, 3 Restart, 4 Abort, 5 Reset)"),
        num("st", "STATE", "Batch state (0 Idle, 1 Running, 2 Held, 3 Complete, 4 Aborted)", True, True),
        num("step", "STEP", "Engine step (1 prep, 2 start, 3 run, 4 reset, 5 fermenter wait, 6 holding, 7 held, 8 restarting, 9 aborting, 10 abort reset)", True),
        num("seq_ix", "SEQ_INDEX", "Index of the phase run in the brew's sequence", True),
        num("resume", "RESUME_STEP", "Step to resume at after a hold", True),
        num("why", "REASON", "Why the batch is held, waiting, refused, or aborted (code)", True, True),
        num("number", "NUMBER", "Batch number (the last one started)", True, True),
        num("completed", "COMPLETED", "Brews completed", True, True),
        num("queue", "QUEUE", "Brews queued", True, True),
        num("handled", "REQUESTS_HANDLED", "Panel requests the engine has taken (16-bit counter)", True),
        num("fv", "FV", "Fermenter reserved for the brew day (1 to 4, 0 none)", True, True),
        num("fv_brews", "FV_BREWS", "Brews knocked out into the reserved fermenter", True),
        num("hold_req", "HOLD_REQUEST", "A Hold waits for a safe point", True),
        num("t_batch", "T_BATCH", "Batch start (Unix s)", True),
        num("t_phase", "T_PHASE", "Phase start (Unix s)", True),
        num("t_step", "T_STEP", "Step start (Unix s)", True),
        num("t_fv", "T_FV", "First knockout into the reserved fermenter (Unix s)", True),
        num("alm_timeout", "ALM_TIMEOUT", "Alarm: a phase timed out and the batch is held", True, True),
        num("alm_plc", "ALM_PLC", "Alarm: the brewhouse PLC did not answer, lost, or aborted the phase", True, True),
        num("alm_refused", "ALM_REFUSED", "Alarm: a batch start was refused", True, True),
        num("alm_nofv", "ALM_NOFV", "Alarm: no fermenter is ready for knockout", True, True),
        num("alm_engine", "ALM_ENGINE", "Alarm: the batch engine script is failing", True, True),
        num("phase", "PHASE", "Phase number in force (0 none)", False, True),
        num("plc_mirror", "PLC_STATE", "Brewhouse PLC phase state (-1 no link)", False, True),
        num("wait", "WAITING", "Waiting for a fermenter", False, True),
        num("beat", "HEARTBEAT", "Engine heartbeat (Unix s)"),
        text("state_text", "STATE_TEXT", "Batch state"),
        text("status", "STATUS", "Batch status"),
        text("id", "ID", "Batch ID"),
        text("recipe", "RECIPE", "Recipe"),
        text("phase_name", "PHASE_NAME", "Phase"),
        text("fv_name", "FV_NAME", "Reserved fermenter"),
        text("start", "STARTED", "Batch start"),
        text("phase_start", "PHASE_STARTED", "Phase start"),
    )


BATCH_TAGS: tuple[BatchTag, ...] = _batch_tags()

#: The plant tags the engine reads and writes, by the engine's own names.
BATCH_PLANT_TAGS: dict[str, str] = {
    "plc_state": "BH.BATCH.XS01.STATE",
    "plc_cmd": "BH.BATCH.XC01.CMD",
    "plc_phase": "BH.BATCH.XC01.PHASE",
    "plc_param": "BH.BATCH.XC03.PARAM",
    "plc_batch": "BH.BATCH.XI01.BATCH",
    "requests": "BH.BATCH.XI02.REQUESTS",
    "plc_accepted": "BH.BATCH.XC02.ACCEPTED",
    "lock_mlt": "BH.ENTRY.XS01.LOCKED",
    "lock_ktl": "BH.ENTRY.XS02.LOCKED",
    "sp_hlt": "BH.HLT.TT01.SP",
    "sp_mlt": "BH.MLT.TT01.SP",
    "sp_ko": "BH.KO.TT02.SP",
}
#: What plc-cellar reports of each fermenter: the extract meter (0 when empty),
#: the entry lockout, and the CIP permit (withdrawn during entry).
BATCH_FV_TAGS: dict[str, str] = {
    "fv{n}_extract": "CEL.FV10{n}.DT01.PV",
    "fv{n}_lock": "CEL.ENTRY.XS0{n}.LOCKED",
    "fv{n}_cip": "CEL.ENTRY.XS1{n}.CIP_OK",
}


def _beerjson_f(node: dict[str, Any]) -> float:
    value: float = float(node["value"])
    unit: str = node["unit"]
    if unit == "F":
        return value
    if unit == "C":
        return value * 1.8 + 32.0
    raise SystemExit(f"{RECIPE_PATH.name}: temperature unit {unit!r} is not F or C")


def _beerjson_kg(node: dict[str, Any]) -> float:
    factors: dict[str, float] = {"kg": 1.0, "g": 0.001, "lb": 0.45359237, "oz": 0.028349523}
    unit: str = node["unit"]
    if unit not in factors:
        raise SystemExit(f"{RECIPE_PATH.name}: mass unit {unit!r} is not kg, g, lb, or oz")
    return float(node["value"]) * factors[unit]


def _beerjson_min(node: dict[str, Any]) -> int:
    factors: dict[str, float] = {"min": 1.0, "hr": 60.0, "day": 1440.0}
    unit: str = node["unit"]
    if unit not in factors:
        raise SystemExit(f"{RECIPE_PATH.name}: time unit {unit!r} is not min, hr, or day")
    return round(float(node["value"]) * factors[unit])


def whole_degrees(temp_f: float) -> int:
    """A temperature as a whole-degree setpoint. FUXA's OPC UA driver turns a
    value into an integer before it applies a tag's linear scale
    (server/runtime/devices/opcua/index.js ``setValue``: ``_toValue``, then
    ``tagRawCalculator``), so a fractional setpoint would lose its fraction
    unpredictably; whole degrees survive, and a brewer sets whole degrees."""
    return math.floor(temp_f + 0.5)


def load_batch_recipe(path: Path = RECIPE_PATH) -> BatchRecipe:
    """The engine's parameters from the BeerJSON recipe: the mash steps, the boil
    and the whirlpool stand, the pitching temperature, and the strike water
    temperature (Palmer: 0.41 / ratio x (mash - grain) + mash in metric units,
    plus the line loss, as the sim's brewhouse operator computes it)."""
    try:
        doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        r: dict[str, Any] = doc["beerjson"]["recipes"][0]
        grain_kg: float = sum(_beerjson_kg(f["amount"]) for f in r["ingredients"]["fermentable_additions"])
        steps: list[dict[str, Any]] = r["mash"]["mash_steps"]
        mash: list[MashStep] = [
            MashStep(name=s["name"], temp_f=_beerjson_f(s["step_temperature"]),
                     minutes=_beerjson_min(s["step_time"]))
            for s in steps
        ]
        whirlpool: dict[str, Any] = next(
            s for s in r["boil"]["boil_steps"] if "whirlpool" in s["name"].lower()
        )
        infusion_l: float = float(steps[0]["amount"]["value"])
        if steps[0]["amount"]["unit"] != "l":
            raise SystemExit(f"{path.name}: the first mash step's amount is not in liters")
        grain_f: float = _beerjson_f(r["mash"]["grain_temperature"])
        pitch_f: float = _beerjson_f(r["fermentation"]["fermentation_steps"][0]["start_temperature"])
        boil_minutes: int = _beerjson_min(r["boil"]["boil_time"])
        whirlpool_minutes: int = _beerjson_min(whirlpool["step_time"])
        whirlpool_f: float = _beerjson_f(whirlpool["start_temperature"])
        name: str = r["name"]
    except (OSError, ValueError, KeyError, IndexError, StopIteration) as ex:
        raise SystemExit(f"{path}: cannot read the recipe ({type(ex).__name__}: {ex})") from ex
    to_c = lambda f: (f - 32.0) / 1.8  # noqa: E731
    strike_c: float = (
        0.41 / (infusion_l / grain_kg) * (to_c(mash[0].temp_f) - to_c(grain_f))
        + to_c(mash[0].temp_f)
        + STRIKE_LOSS_K
    )
    return BatchRecipe(
        code=BATCH_RECIPE_CODE,
        name=name,
        grain_kg=round(grain_kg, 1),
        mash=mash,
        boil_minutes=boil_minutes,
        whirlpool_minutes=whirlpool_minutes,
        whirlpool_temp_f=whirlpool_f,
        pitch_temp_f=pitch_f,
        strike_temp_f=whole_degrees(strike_c * 1.8 + 32.0),
        sparge_temp_f=mash[-1].temp_f,
    )


def batch_sequence(recipe: BatchRecipe) -> list[SequenceStep]:
    """The phase runs of one brew: the phases in order, with MASH_STEP once per
    mash step of the recipe. Each carries the setpoints written before its Start
    (the HLT at the strike temperature until the mash is in, then at the
    sparge temperature; the MLT at the step being run; the knockout at the
    pitching temperature) and the phase parameter."""
    spec: dict[int, PhaseSpec] = {p.number: p for p in PHASES}
    runs: list[SequenceStep] = []

    def add(n: int, param: int, mlt_f: float, label: str) -> None:
        p: PhaseSpec = spec[n]
        runs.append(SequenceStep(
            n=n,
            name=p.name,
            label=label,
            param=param,
            hlt=whole_degrees(recipe.strike_temp_f if n <= 3 else recipe.sparge_temp_f),
            mlt=whole_degrees(mlt_f),
            ko=whole_degrees(recipe.pitch_temp_f),
            timeout_s=p.base_s + p.per_unit_s * param,
        ))

    first, last = recipe.mash[0], recipe.mash[-1]
    add(1, round(recipe.grain_kg), first.temp_f, "MILL")
    add(2, 0, first.temp_f, "HLT_HEAT")
    add(3, 0, first.temp_f, "MASH_IN")
    for i, step in enumerate(recipe.mash, start=1):
        label: str = f"MASH_STEP {i} of {len(recipe.mash)} ({step.name}, {step.temp_f:g} F)"
        add(4, step.minutes, step.temp_f, label)
    add(5, 0, last.temp_f, "VORLAUF")
    add(6, 0, last.temp_f, "LAUTER")
    add(7, recipe.boil_minutes, last.temp_f, "BOIL")
    add(8, recipe.whirlpool_minutes, last.temp_f, "WHIRLPOOL")
    add(9, 0, last.temp_f, "KNOCKOUT")
    add(10, 0, last.temp_f, "CIP_READY")
    return runs


def batch_names() -> dict[str, str]:
    """Every tag the engine touches, by its own name for it: the PLC and cellar
    tags and the FUXA Server tags."""
    names: dict[str, str] = dict(BATCH_PLANT_TAGS)
    for n in range(1, FERMENTER_COUNT + 1):
        for key, name in BATCH_FV_TAGS.items():
            names[key.format(n=n)] = name.format(n=n)
    own: dict[str, str] = {t.key: t.name for t in BATCH_TAGS}
    clash: set[str] = names.keys() & own.keys()
    if clash or len(own) != len(BATCH_TAGS):
        raise SystemExit(f"batch engine tag keys are not unique: {sorted(clash)}")
    names.update(own)
    return names


def batch_config(recipe: BatchRecipe) -> dict[str, Any]:
    """The data the engine script is built with."""
    return {
        "recipe": recipe.model_dump(),
        "timing": BatchTiming().model_dump(),
        "fermenters": FERMENTER_COUNT,
        "sequence": [s.model_dump() for s in batch_sequence(recipe)],
        "names": batch_names(),
    }


def batch_script(recipe: BatchRecipe) -> dict[str, Any]:
    """The engine as a FUXA server script: run every second by FUXA's scheduler
    (``scheduling.mode`` ``interval``, in seconds)."""
    source: str = BATCH_ENGINE_JS.read_text(encoding="utf-8")
    marker: str = "const CFG = __CFG__;"
    if source.count(marker) != 1:
        raise SystemExit(f"{BATCH_ENGINE_JS.name} must hold {marker!r} exactly once")
    config: str = json.dumps(batch_config(recipe), indent=1, sort_keys=True)
    return {
        "id": uid("s_", BATCH_SCRIPT_NAME),
        "name": BATCH_SCRIPT_NAME,
        "code": source.replace(marker, f"const CFG = {config};"),
        "sync": False,
        "parameters": [],
        "scheduling": {"mode": "interval", "interval": 1},
        "mode": "SERVER",
    }


def batch_tag_ids() -> dict[str, str]:
    """Tag name -> tag id of the engine's FUXA Server tags (stable across builds)."""
    return {t.name: uid("t_", f"fuxa-server/{t.name}") for t in BATCH_TAGS}


def batch_server_device(recipe: BatchRecipe) -> ServerDevice:
    """The ``FuxaServer`` device with the engine's tags. Restored tags keep the
    batch state across a FUXA restart (FUXA stores them as current values);
    historian tags also go to the DAQ store."""
    ids: dict[str, str] = batch_tag_ids()
    tags: dict[str, ServerTag] = {}
    for t in BATCH_TAGS:
        init: str = recipe.name if t.key == "recipe" else ("0" if t.kind == "number" else "")
        tags[ids[t.name]] = ServerTag(
            id=ids[t.name],
            name=t.name,
            label=t.label,
            type=t.kind,
            init=init,
            description=t.label,
            daq=TagDaq(enabled=t.historian, changed=t.historian, interval=60, restored=t.persist),
        )
    return ServerDevice(
        id=FUXA_SERVER_ID,
        name="FUXA Server",
        enabled=True,
        type="FuxaServer",
        property={},
        polling=1000,
        tags=tags,
    )


#: The engine's own alarms: (tag key, FUXA severity, text). ISA-18.2 priority:
#: High for what holds a batch, Medium for wort waiting, Low for a refused start.
BATCH_ALARMS: tuple[tuple[str, str, str], ...] = (
    ("alm_timeout", "high", "A batch phase did not complete in time and the batch is held: check the phase's equipment, then Restart or Abort"),
    ("alm_plc", "high", "The brewhouse PLC did not answer, lost, or aborted a batch phase: check plc-brewhouse, then Restart or Abort"),
    ("alm_engine", "high", "The batch engine script is failing: see the FUXA log"),
    ("alm_nofv", "low", "Knockout is waiting for a fermenter that is empty and lined up: the wort stays in the kettle"),
    ("alm_refused", "info", "A batch start is refused (brewhouse not Idle, a vessel in entry, or no fermenter empty and lined up): clear the cause, or Reset to drop the queue"),
)


def build_batch_alarms() -> list[dict[str, Any]]:
    """One FUXA alarm per engine alarm tag, active while the tag is 1."""
    ids: dict[str, str] = batch_tag_ids()
    names: dict[str, str] = {t.key: t.name for t in BATCH_TAGS}
    alarms: list[dict[str, Any]] = []
    for key, severity, text in BATCH_ALARMS:
        alarm: dict[str, Any] = build_alarm(
            PlantAlarm(name=names[key], fuxa_severity=severity, fuxa_tag_id=ids[names[key]], text=text)
        )
        for level in ("highhigh", "high", "low", "info"):
            alarm[level] = sub_property(severity, level, text, "Batch")
        alarms.append(alarm)
    return alarms


def _lb(kg: float) -> str:
    return f"{kg * 2.20462:,.0f} lb"


def build_batch_view(recipe: BatchRecipe) -> dict[str, Any]:
    """The Batch view of scada-fuxa: the operator's Start, Hold, Restart, Abort,
    and Reset, the batch's state and phase, the queue, the reserved fermenter,
    the engine's alarms, and the recipe the batch follows."""
    ids: dict[str, str] = batch_tag_ids()
    internal: dict[str, str] = {t.name: ids[t.name] for t in BATCH_TAGS}
    width, height = 820, 610
    vb: ViewBuilder = ViewBuilder({}, width=width, height=height, internal=internal)
    vb.text(LEFT, 28, f"Batch: {recipe.name}", size=18, bold=True)
    rows: tuple[tuple[str, str, int, str], ...] = (
        ("Batch ID", "BATCH.ID", 0, ""),
        ("State", "BATCH.STATE_TEXT", 0, ""),
        ("Phase", "BATCH.PHASE_NAME", 0, ""),
        ("Status", "BATCH.STATUS", 0, ""),
        ("Fermenter", "BATCH.FV_NAME", 0, ""),
        ("Brews queued", "BATCH.QUEUE", 0, ""),
        ("Brews completed", "BATCH.COMPLETED", 0, ""),
        ("Batch started", "BATCH.STARTED", 0, ""),
        ("Phase started", "BATCH.PHASE_STARTED", 0, ""),
        ("PLC phase state", "BATCH.PLC_STATE", 0, ""),
        ("Engine heartbeat (Unix s)", "BATCH.HEARTBEAT", 0, ""),
    )
    for i, (caption, tag, digits, unit) in enumerate(rows):
        y: int = 66 + 26 * i
        vb.text(LEFT, y, caption, size=12)
        vb.value(LEFT + 190, y, tag, digits=digits, unit=unit, anchor="start")
    y0: int = 66 + 26 * len(rows) + 14
    vb.text(LEFT, y0, "PLC phase state: 0 Idle, 1 Running, 2 Holding, 3 Held, 4 Restarting, 5 Complete, 6 Aborted", size=11)
    vb.text(LEFT, y0 + 40, "Operator", size=12, bold=True)
    for i, (caption, code) in enumerate((("Start", 1), ("Hold", 2), ("Restart", 3), ("Abort", 4), ("Reset", 5))):
        vb.button(LEFT + 90 + 100 * i, y0 + 20, 90, 32, caption, "BATCH.CMD", code)
    vb.text(LEFT, y0 + 84, "Alarms", size=12, bold=True)
    for i, (key, _severity, _text) in enumerate(BATCH_ALARMS):
        lx: int = LEFT + 100 + 110 * i
        vb.lamp(lx, y0 + 80, f"BATCH.{key.upper()}", "#e53935")
        vb.text(lx + 14, y0 + 84, key.removeprefix("alm_").capitalize(), size=11)
    vb.text(LEFT, y0 + 118, "Phases", size=12, bold=True)
    for i, p in enumerate(PHASES):
        px: int = LEFT + 100 + 68 * i
        vb.number_lamp(px, y0 + 114, "BATCH.PHASE", p.number, "#1e88e5")
        vb.text(px, y0 + 138, p.name.replace("_", " ").lower(), size=9, anchor="middle")
    recipe_lines: list[str] = [
        f"Grain bill {_lb(recipe.grain_kg)}, strike water {recipe.strike_temp_f:g} F, sparge at {recipe.sparge_temp_f:g} F",
        "Mash: " + ", then ".join(f"{s.name} {s.temp_f:g} F for {s.minutes} min" for s in recipe.mash),
        f"Boil {recipe.boil_minutes} min, whirlpool stand {recipe.whirlpool_minutes} min at {recipe.whirlpool_temp_f:g} F, knockout and pitch at {recipe.pitch_temp_f:g} F",
    ]
    for i, line in enumerate(recipe_lines):
        vb.text(LEFT, y0 + 172 + 20 * i, line, size=12)
    view_id: str = uid("v_", "Batch")
    return {
        "id": view_id,
        "name": "Batch",
        "profile": {
            "width": width,
            "height": height,
            "bkcolor": BRAND.process_bkcolor(),
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


def check_batch_tags(tags: dict[str, PlantTag]) -> None:
    """Every plant tag the engine uses must be in the tag list."""
    missing: list[str] = sorted(
        name for key, name in batch_names().items()
        if not name.startswith("BATCH.") and name not in tags
    )
    if missing:
        raise SystemExit(f"the batch engine needs these tags, missing from the tag list: {missing}")


def graphics_plant(plant: PlantTags, gen: Path) -> hmi_views.Plant:
    """What the process graphics are drawn from: the tags by name, the alarm
    records of ``alarms.json`` by the tag they watch, and each tag's device."""
    by_source: dict[str, list[dict[str, Any]]] = {}
    for alarm in json.loads((gen / "alarms.json").read_text(encoding="utf-8"))["alarms"]:
        by_source.setdefault(alarm["source"], []).append(alarm)
    return hmi_views.Plant(
        tags={t.name: t for d in plant.devices for t in d.tags},
        alarms=by_source,
        devices={t.name: d.name for d in plant.devices for t in d.tags},
    )


def build_plant_project(variant: str, host: str) -> Project:
    gen: Path = PLANT_GENERATED / variant
    path: Path = gen / "fuxa" / f"{host}-tags.json"
    if not path.is_file():
        raise SystemExit(f"no tag list for {host} in variant {variant}")
    plant: PlantTags = PlantTags.model_validate_json(path.read_text(encoding="utf-8"))
    alarms: PlantAlarms = PlantAlarms.model_validate_json((gen / "alarms.json").read_text(encoding="utf-8"))
    tags: dict[str, PlantTag] = {t.name: t for d in plant.devices for t in d.tags}
    ids: set[str] = {t.id for t in tags.values()}
    screens: list[hmi_views.Screen] = hmi_views.screens(graphics_plant(plant, gen))
    views: list[dict[str, Any]] = [s.view for s in screens]
    charts: list[dict[str, Any]] = [chart for s in screens for chart in s.charts]
    trended: frozenset[str] = frozenset(line["id"] for chart in charts for line in chart["lines"])
    hand_laid: frozenset[str] = frozenset(v["name"] for v in views)
    views += [build_device_view(d, tags, hand_laid) for d in plant.devices]
    devices: dict[str, FuxaDevice | ServerDevice] = {
        d.id: d for d in (convert_plant_device(d, trended) for d in plant.devices)
    }
    project_alarms: list[dict[str, Any]] = [
        build_plant_alarm(a) for a in alarms.alarms if a.fuxa_tag_id in ids
    ]
    scripts: list[dict[str, Any]] = []
    if host == "scada-fuxa" and BATCH_PLC_STATE_TAG in tags:
        # The brewhouse PLC is here, so the ISA-88 batch engine sequences it:
        # its script, its FUXA Server tags and alarms, and the Batch view.
        check_batch_tags(tags)
        recipe: BatchRecipe = load_batch_recipe()
        brewhouse: int = next((i for i, v in enumerate(views) if v["name"] == "Brewhouse"), -1)
        views.insert(brewhouse + 1, build_batch_view(recipe))
        devices[FUXA_SERVER_ID] = batch_server_device(recipe)
        project_alarms += build_batch_alarms()
        scripts.append(batch_script(recipe))
    names: list[str] = [v["name"] for v in views]
    if len(set(names)) != len(names):
        # FUXA finds a view by its name, so a second one of the same name
        # cannot be opened by URL.
        raise SystemExit(f"two views of {host} share a name: {sorted(names)}")
    return Project(
        name=f"cherrymountain-{variant}-{host}",
        hmi={"layout": build_plant_layout(views), "views": views},
        devices=devices,
        alarms=project_alarms,
        charts=charts,
        scripts=scripts,
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

    by_id: dict[str, PlantTag] = {t.id: t for t in plant.devices[0].tags}
    missing: list[str] = [a.name for a in alarms.alarms if a.fuxa_tag_id not in by_id]
    if missing:
        raise SystemExit(f"alarm tags missing from the tag list: {missing}")

    screen: hmi_views.Screen = hmi_views.cellar(graphics_plant(plant, gen))
    device: FuxaDevice = convert_device(plant.devices[0])
    return Project(
        hmi={"layout": build_layout(screen.view["id"]), "views": [screen.view]},
        devices={device.id: device},
        alarms=[build_alarm(a) for a in alarms.alarms],
        charts=screen.charts,
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
