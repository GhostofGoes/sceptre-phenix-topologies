"""What the FUXA project builder and its process graphics share.

``build_project.py`` assembles a project; ``hmi_kit.py`` and ``hmi_views.py``
draw its process graphics. All three need the same few things: stable ids, the
brand's colors, the plantgen tag and alarm records, and the plain view builder
that the generated tables and the Batch view are written with. They live here
so that the graphics do not import the builder that imports them.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

HERE: Path = Path(__file__).resolve().parent
PLANT_GENERATED: Path = HERE.parents[0] / "plant" / "generated"
BRAND_TOKENS: Path = HERE.parent / "brand" / "tokens.json"
NAMESPACE: uuid.UUID = uuid.uuid5(uuid.NAMESPACE_URL, "cherrymountain-brewery/fuxa")
OUTPUT_UNIT: int = 1  # PropertyType.output in the FUXA editor (unit and digits)


def uid(prefix: str, name: str) -> str:
    """Stable ID for ``name``: ``<prefix>_<uuid5>``."""
    return f"{prefix}{uuid.uuid5(NAMESPACE, name)}"


# ---------------------------------------------------------------- brand


class Brand(BaseModel):
    """``brand/tokens.json``: the colors by name, the roles that name them, the
    colors reserved for alarms, and the process area's gray."""

    model_config = ConfigDict(extra="ignore")

    colors: dict[str, str]
    roles: dict[str, str | list[str]]
    reserved_for_alarms: dict[str, str]
    process_area: dict[str, str]

    def color(self, name: str) -> str:
        """The ``#RRGGBB`` value of a named color."""
        return self.colors[name]

    def role(self, role: str) -> str:
        """The value of the color a role names (``header_bg`` -> ink -> ``#1B1B1F``)."""
        name: str | list[str] = self.roles[role]
        if not isinstance(name, str):
            raise ValueError(f"role {role} names several colors, not one")
        return self.colors[name]

    def alarm(self, level: str) -> str:
        """The color reserved for an alarm level (``critical``, ``high``, ``medium``, ``low``)."""
        return self.reserved_for_alarms[level]

    def process_bkcolor(self) -> str:
        """The process area's background as FUXA writes a view's ``bkcolor``
        (lowercase, with an alpha byte)."""
        return self.process_area["background"].lower() + "ff"


def load_brand(path: Path = BRAND_TOKENS) -> Brand:
    if not path.is_file():
        raise SystemExit(f"missing {path}: the FUXA frame takes its colors from the brand tokens")
    return Brand.model_validate_json(path.read_text(encoding="utf-8"))


BRAND: Brand = load_brand()


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
    delay_s: float = 0.0
    evaluator: str | None = None


class PlantAlarms(BaseModel):
    model_config = ConfigDict(extra="ignore")

    alarms: list[PlantAlarm]


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

    def __init__(
        self,
        tags: dict[str, PlantTag],
        width: int,
        height: int,
        internal: dict[str, str] | None = None,
    ) -> None:
        self.tags: dict[str, PlantTag] = tags
        #: Tag name -> tag id of FUXA's own (FUXA Server device) tags.
        self.internal: dict[str, str] = internal or {}
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

    def _tag_id(self, name: str) -> str:
        """The id of a plant tag, or of one of FUXA's own tags."""
        if name in self.internal:
            return self.internal[name]
        return self._tag(name).id

    def value(
        self, x: int, y: int, tag_name: str, digits: int, unit: str, anchor: str = "middle"
    ) -> None:
        tag_id: str = self._tag_id(tag_name)
        gid: str = uid("VAL_", tag_name)
        self.svg.append(
            f'<g id="{gid}" type="svg-ext-value" fill="#000000" stroke="#000000"'
            f' font-size="14" stroke-width="0" font-family="sans-serif" text-anchor="{anchor}">'
            f'<text stroke-width="0" id="{uid("VAL_", tag_name + "/text")}" fill="#000000"'
            f' stroke="#000000" font-size="14" font-family="sans-serif" text-anchor="{anchor}"'
            f' xml:space="preserve" x="{x}" y="{y}">##.##</text></g>'
        )
        self.items[gid] = Gauge(
            id=gid,
            type="svg-ext-value",
            name=tag_name,
            label="Value",
            property={
                "events": [],
                "variableId": tag_id,
                "ranges": [
                    {"type": OUTPUT_UNIT, "min": 0, "max": 0, "text": unit,
                     "textId": "", "fractionDigits": digits}
                ],
            },
        )

    def setpoint_input(
        self, x: int, y: int, tag_name: str, lo: float, hi: float, scope: str = ""
    ) -> None:
        """An input box that shows a tag's value and writes what the operator
        enters (FUXA multiplies by the tag's divisor). ``scope`` keeps the
        element ids of a second view of the same tag apart."""
        tag: PlantTag = self._tag(tag_name)
        key: str = scope + tag_name
        gid: str = uid("HXI_", key)
        w, h = 64, 24
        self.svg.append(
            f'<g id="{gid}" stroke="#000000" text-anchor="right" font-family="sans-serif"'
            f' font-size="14" fill="#f1f1f1ff" type="svg-ext-html_input">'
            f'<rect id="{uid("svg_", key + "/rect")}" stroke="null" fill="#ffffff"'
            f' height="{h}" width="{w}" y="{y}" x="{x}" stroke-width="0"/>'
            f'<foreignObject id="H-{uid("HXI_", key + "/fo")}" width="{w}" height="{h}"'
            f' y="{y}" x="{x}"><INPUT id="I-{uid("HXI_", key + "/in")}"'
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
        tag_id: str = self._tag_id(tag_name)
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
                "variableId": tag_id,
                "ranges": [
                    {"min": 0, "max": 0, "color": "#bdbdbd", "stroke": "#616161"},
                    {"min": 1, "max": 1, "color": on_color, "stroke": "#212121"},
                ],
            },
        )

    def number_lamp(self, cx: int, cy: int, tag_name: str, number: int, on_color: str) -> None:
        """A lamp that is lit while a tag holds ``number`` (0 to 99), grey otherwise."""
        tag_id: str = self._tag_id(tag_name)
        gid: str = uid("GSE_", f"{tag_name}={number}")
        self.svg.append(
            f'<g id="{gid}" type="svg-ext-gauge_semaphore" stroke="#616161" stroke-width="1">'
            f'<circle id="{uid("GSE_", f"{tag_name}={number}/c")}" cx="{cx}" cy="{cy}" r="8"'
            f' fill="#bdbdbd" stroke="#616161"/></g>'
        )
        grey: dict[str, Any] = {"color": "#bdbdbd", "stroke": "#616161"}
        ranges: list[dict[str, Any]] = [
            {"min": 0, "max": number - 1, **grey},
            {"min": number, "max": number, "color": on_color, "stroke": "#212121"},
            {"min": number + 1, "max": 99, **grey},
        ]
        self.items[gid] = Gauge(
            id=gid,
            type="svg-ext-gauge_semaphore",
            name=f"{tag_name}={number}",
            label="HtmlSemaphore",
            property={"events": [], "variableId": tag_id, "ranges": ranges},
        )

    def button(self, x: int, y: int, w: int, h: int, text: str, tag_name: str, value: int) -> None:
        """A button that writes ``value`` to a tag: the editor's ``html_button``
        with its ``set`` action. The editor draws the group with
        ``pointer-events:none`` and drops that style when it saves the view; a
        group that keeps it never gets the click (seen in FUXA 1.3.6: the view's
        ``svg`` takes it instead), so this one is written as a saved button is."""
        tag_id: str = self._tag_id(tag_name)
        gid: str = uid("HXB_", f"{tag_name}/{value}")
        style: str = (
            "width:calc(100% - 6px);height:calc(100% - 6px);text-align:center;"
            "background-color:rgba(238,238,238,1);color:rgba(33,33,33,1);"
            "font-size:14px;font-family:sans-serif;"
        )
        self.svg.append(
            f'<g id="{gid}" type="svg-ext-html_button"'
            f' fill="rgba(0,0,0,0)" stroke="rgba(0,0,0,0)" xml:space="preserve">'
            f'<rect id="{uid("svg_", f"{tag_name}/{value}/rect")}" x="{x}" y="{y}" width="{w}"'
            f' height="{h}" stroke-width="0"/>'
            f'<foreignObject id="H-{uid("HXB_", f"{tag_name}/{value}/fo")}" x="{x}" y="{y}"'
            f' width="{w}" height="{h}"><button id="B-{uid("HXB_", f"{tag_name}/{value}/b")}"'
            f' class="md-btn  md-btn-raised" style="{style}">{text}</button></foreignObject></g>'
        )
        self.items[gid] = Gauge(
            id=gid,
            type="svg-ext-html_button",
            name=text,
            label="HtmlButton",
            property={
                "events": [],
                "actions": [],
                "variableId": tag_id,
                "text": text,
                "buttonAction": {"mode": "set", "setValue": str(value)},
            },
        )

    def svg_content(self) -> str:
        body: str = "\n  ".join(self.svg)
        return (
            f'<svg width="{self.width}" height="{self.height}"'
            f' xmlns="http://www.w3.org/2000/svg" xmlns:svg="http://www.w3.org/2000/svg">'
            f"\n <g>\n  <title>Layer 1</title>\n  {body}\n </g>\n</svg>"
        )
