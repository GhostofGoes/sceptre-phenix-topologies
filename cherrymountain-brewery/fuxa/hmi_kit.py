"""Process graphics for the FUXA projects: the drawing kit.

The generated tag lists ("... points" views) show every value as a number. An
operator needs the plant instead: vessels that show what is in them, scales
that show where a value sits in its range, equipment that shows whether it
runs, pipes that show where product goes. This module is the kit those
graphics are drawn with, and ``hmi_views.py`` lays the screens out with it.

The style follows the high-performance HMI practice of ISA-101: a quiet gray
picture in which color means something.

* **Gray is normal.** Equipment is drawn in grays on a light gray ground. A
  screen with nothing wrong has no red or yellow on it anywhere.
* **Alarm colors are only alarms.** The four colors of ``brand/tokens.json``
  (``reserved_for_alarms``) appear only in an alarm flag, and a flag is also a
  shape with the priority's number in it, so it reads without color.
* **Media have their own muted colors**, used for what is in a vessel or a pipe
  and nothing else: wort, beer, water, glycol, steam, CO2, CIP solution. One
  legend holds for every screen.
* **Running is filled, stopped is hollow.** A pump, valve, fan, or breaker that
  is on is filled with one "live" color; one that is off is an outline. A fan
  that runs also turns, and a pipe with flow in it shows the flow moving.
* **A number comes with its range.** A measurement that has limits is drawn on
  a scale: the normal band, the alarm limits as marks, and a pointer that moves
  with the value. A quantity (a level, a charge, a valve's opening) is a fill.
* **Flow reads left to right**, product on top and utilities below, and every
  screen keeps a vessel where the overview put it.

How each element is written for FUXA 1.3.6 (all of them were run in a real
server; ``README.md`` has the list):

* a number is an ``svg-ext-value``; a lamp an ``svg-ext-gauge_semaphore``;
* a shape that takes a color from a tag (a valve, a breaker, a wire) is a plain
  SVG element whose item has ``ranges``;
* a fill and a pointer are plain SVG elements with a ``moveByTags`` action: FUXA
  moves them between two positions as the tag goes from one value to another,
  and holds them at the end positions outside that span. A fill moves behind a
  clip path, so it takes the vessel's shape;
* an alarm flag and a state's word are hidden elements with ``show`` and
  ``hide`` actions;
* a fan's blades turn with the ``clockwise`` action; a pipe with flow is an
  ``svg-ext-pipe``, whose dashes FUXA scrolls;
* a trend is an ``svg-ext-html_chart`` over one of the project's ``charts``;
* a link is a transparent rectangle with an ``onpage`` click event.
"""

from __future__ import annotations

from typing import Any, Final, NamedTuple

from hmi_base import BRAND, OUTPUT_UNIT, Gauge, PlantTag, ViewBuilder, uid

# ---------------------------------------------------------------- palette

#: The picture's own grays (none is a brand accent or an alarm color).
INK: Final[str] = "#2F3640"  # outlines of equipment, primary text
DIM: Final[str] = "#6B7280"  # secondary text, units, captions
RULE: Final[str] = "#B8BEC7"  # scales, quiet separators
BODY: Final[str] = "#DDE1E6"  # the body of a vessel or a machine
CARD: Final[str] = "#FBFBFC"  # a panel that groups values
BAND: Final[str] = "#BFD3E0"  # the normal band of a scale
WHITE: Final[str] = "#FFFFFF"
#: On, open, running, closed-in (a breaker): the one "live" color.
LIVE: Final[str] = "#2B6C8A"
#: What flows and what a vessel holds.
MEDIA: Final[dict[str, str]] = {
    "wort": "#B9873E",
    "beer": "#D9B44A",
    "water": "#7FA8C9",
    "hot": "#C98C6B",
    "glycol": "#62B0A8",
    "steam": "#AEB6C2",
    "co2": "#9BB39A",
    "cip": "#A592C4",
    "air": "#B4C3D3",
    "grain": "#C4AE8E",
    "power": "#3F4854",
}
#: Alarm priority -> (color, the number in its flag, the color of that number).
#: The colors are the brand's reserved four; the flag's shape (square, triangle,
#: inverted triangle, diamond) and its number make it readable without them.
ALARM_LEVELS: Final[dict[str, tuple[str, int, str]]] = {
    "critical": (BRAND.reserved_for_alarms["critical"], 1, WHITE),
    "high": (BRAND.reserved_for_alarms["high"], 2, WHITE),
    "medium": (BRAND.reserved_for_alarms["medium"], 3, INK),
    "low": (BRAND.reserved_for_alarms["low"], 4, INK),
}
FONT: Final[str] = "sans-serif"
#: "Any value beyond this" in a range (FUXA compares numbers, so an open range
#: is written with a far end).
FAR: Final[float] = 1.0e9
#: The side of an alarm flag, in pixels.
FLAG: Final[int] = 14
#: Minutes a trend shows.
TREND_MINUTES: Final[int] = 60

Json = dict[str, Any]
Alarm = dict[str, Any]


class Screen(NamedTuple):
    """A finished view and the trend definitions it needs in the project."""

    view: Json
    charts: list[Json]


def action(tag_id: str, kind: str, lo: float, hi: float, **options: Any) -> Json:
    """One entry of an item's ``actions``: what ``kind`` does while the tag is in
    ``[lo, hi]`` (``moveByTags`` follows the tag instead and ignores the range)."""
    return {"variableId": tag_id, "range": {"min": lo, "max": hi}, "type": kind, "options": options}


def shown(tag_id: str, lo: float, hi: float) -> list[Json]:
    """The actions of an element that is visible only while its tag is in
    ``[lo, hi]`` (it starts hidden: see ``Canvas.hidden``)."""
    gap: float = 1.0e-6
    return [
        action(tag_id, "show", lo, hi),
        action(tag_id, "hide", -FAR, lo - gap),
        action(tag_id, "hide", hi + gap, FAR),
    ]


class Canvas(ViewBuilder):
    """A view under construction: static drawing, then the live elements."""

    def __init__(self, name: str, tags: dict[str, PlantTag], width: int, height: int,
                 alarms: dict[str, list[Alarm]] | None = None,
                 internal: dict[str, str] | None = None,
                 devices: dict[str, str] | None = None) -> None:
        super().__init__(tags, width, height, internal)
        #: The view's name: part of every element id, so two views that are on
        #: screen together (a card over a view) never share one.
        self.name: str = name
        #: Source tag -> its alarms (limit, priority, the tag the alarm is read from).
        self.alarms: dict[str, list[Alarm]] = alarms or {}
        #: Tag name -> the name of the device it is read from (a trend's legend).
        self.devices: dict[str, str] = devices or {}
        self.defs: list[str] = []
        self.charts: list[Json] = []
        #: Items that start hidden (FUXA's ``hide`` flag of an item).
        self.hidden: set[str] = set()
        #: Stand-ins for the moving elements, for a picture drawn outside FUXA.
        self.mock: list[str] = []
        self._keys: dict[str, int] = {}

    # -- plumbing ----------------------------------------------------------------------

    def key(self, kind: str, *parts: object) -> str:
        """A key for an element id: the view, the kind, and what tells this
        element from the others of its kind (a tag, a position)."""
        base: str = "/".join([self.name, kind, *(f"{p:g}" if isinstance(p, float) else str(p) for p in parts)])
        n: int = self._keys.get(base, 0)
        self._keys[base] = n + 1
        return base if n == 0 else f"{base}#{n}"

    def has(self, *tag_names: str) -> bool:
        """True when the project has every one of these tags (a variant may not)."""
        return all(n in self.tags or n in self.internal for n in tag_names)

    def tid(self, tag_name: str) -> str:
        return self._tag_id(tag_name)

    def writable(self, tag_name: str) -> None:
        """Refuse a control on a tag the plant model does not let an operator
        write: the controller would refuse the write, or worse, take it."""
        if tag_name in self.tags and self.tags[tag_name].access != "read-write":
            raise SystemExit(f"{self.name}: a control on {tag_name}, which is not read-write in the plant model")

    def item(self, gid: str, kind: str, label: str, name: str, prop: Json, hide: bool = False) -> None:
        self.items[gid] = Gauge(id=gid, type=kind, name=name, label=label, property=prop)
        if hide:
            self.hidden.add(gid)

    def shape(self, element: str, attrs: Json, name: str, tag_name: str | None = None,
              ranges: list[Json] | None = None, actions: list[Json] | None = None,
              events: list[Json] | None = None, content: str = "", hide: bool = False) -> str:
        """A plain SVG element that reacts to tags: ``ranges`` color it from
        ``tag_name``, ``actions`` move, turn, show, or hide it, ``events`` make it
        a button. FUXA finds such an element by an id that starts with ``svg_``."""
        gid: str = uid("svg_", self.key(element, name, *(attrs.get(k, "") for k in ("x", "y", "d", "cx", "cy"))))
        text: str = " ".join(f'{k}="{v}"' for k, v in attrs.items())
        self.svg.append(f'<{element} id="{gid}" {text}>{content}</{element}>')
        prop: Json = {"events": events or [], "actions": actions or [], "ranges": ranges or []}
        if tag_name is not None:
            prop["variableId"] = self.tid(tag_name)
        self.item(gid, f"svg-ext-shapes-{element}", "Shapes", name, prop, hide)
        return gid

    # -- static drawing ----------------------------------------------------------------

    def label(self, x: float, y: float, content: str, size: int = 12, color: str = DIM,
              anchor: str = "start", bold: bool = False) -> None:
        weight: str = ' font-weight="bold"' if bold else ""
        self.svg.append(
            f'<text xml:space="preserve" x="{x:g}" y="{y:g}" font-size="{size}" font-family="{FONT}"'
            f' text-anchor="{anchor}" fill="{color}" stroke-width="0"{weight}>{content}</text>'
        )

    def title(self, x: float, y: float, content: str) -> None:
        self.label(x, y, content, size=18, color=INK, bold=True)

    def rect(self, x: float, y: float, w: float, h: float, fill: str = BODY, stroke: str = INK,
             width: float = 1.5, radius: float = 0, dash: str = "") -> None:
        extra: str = f' stroke-dasharray="{dash}"' if dash else ""
        self.svg.append(
            f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="{radius:g}" fill="{fill}"'
            f' stroke="{stroke}" stroke-width="{width:g}"{extra}/>'
        )

    def path(self, d: str, fill: str = "none", stroke: str = INK, width: float = 1.5,
             dash: str = "") -> None:
        extra: str = f' stroke-dasharray="{dash}"' if dash else ""
        self.svg.append(
            f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{width:g}"'
            f' stroke-linejoin="round" stroke-linecap="round"{extra}/>'
        )

    def card(self, x: float, y: float, w: float, h: float, heading: str = "") -> None:
        """A quiet panel that groups what belongs together."""
        self.rect(x, y, w, h, fill=CARD, stroke=RULE, width=1, radius=6)
        if heading:
            self.label(x + 10, y + 17, heading.upper(), size=10, color=DIM, bold=True)

    def box(self, x: float, y: float, w: float, h: float, name: str) -> None:
        """A machine or a skid: a plain block with its name."""
        self.rect(x, y, w, h, fill=BODY, radius=4)
        self.label(x + w / 2, y + 16, name, size=12, color=INK, anchor="middle", bold=True)

    def arrow(self, x: float, y: float, direction: str = "right", color: str = INK,
              size: float = 7) -> None:
        s = size
        d: str = {
            "right": f"M{x - s:g} {y - s:g} L{x:g} {y:g} L{x - s:g} {y + s:g} Z",
            "left": f"M{x + s:g} {y - s:g} L{x:g} {y:g} L{x + s:g} {y + s:g} Z",
            "down": f"M{x - s:g} {y - s:g} L{x:g} {y:g} L{x + s:g} {y - s:g} Z",
            "up": f"M{x - s:g} {y + s:g} L{x:g} {y:g} L{x + s:g} {y + s:g} Z",
        }[direction]
        self.path(d, fill=color, stroke=color, width=1)

    @staticmethod
    def unitank_outline(x: float, y: float, w: float, h: float, cone: float) -> str:
        cx: float = x + w / 2
        return (f"M{x:g} {y + 14:g} Q{cx:g} {y - 12:g} {x + w:g} {y + 14:g} L{x + w:g} {y + h:g}"
                f" L{cx + 7:g} {y + h + cone:g} L{cx - 7:g} {y + h + cone:g} L{x:g} {y + h:g} Z")

    @staticmethod
    def tank_outline(x: float, y: float, w: float, h: float) -> str:
        cx: float = x + w / 2
        return (f"M{x:g} {y + 12:g} Q{cx:g} {y - 10:g} {x + w:g} {y + 12:g} L{x + w:g} {y + h - 12:g}"
                f" Q{cx:g} {y + h + 10:g} {x:g} {y + h - 12:g} Z")

    def unitank(self, x: float, y: float, w: float, h: float, cone: float) -> None:
        """A cylindroconical vessel: dished head, straight side, cone, legs."""
        for leg in (x + 8, x + w - 8):
            self.path(f"M{leg:g} {y + h - 4:g} L{leg:g} {y + h + cone + 16:g}", stroke=DIM, width=3)
        self.path(self.unitank_outline(x, y, w, h, cone), fill=BODY)

    def tank(self, x: float, y: float, w: float, h: float) -> None:
        """A vertical vessel with dished ends."""
        self.path(self.tank_outline(x, y, w, h), fill=BODY)

    def outline(self, d: str) -> None:
        """A vessel's outline again, over what was drawn inside it."""
        self.path(d, fill="none")

    # -- numbers, lamps, words ---------------------------------------------------------

    def number(self, x: float, y: float, tag_name: str, digits: int = 1, unit: str = "",
               size: int = 14, anchor: str = "end", bold: bool = True, color: str = INK) -> None:
        """A measured value, right-aligned so a column of them lines up. FUXA
        takes 0 digits as "not set" and shows the tag's own rounding (up to two
        decimals), so a scaled value asked for with 0 digits is shown with one."""
        if digits == 0 and tag_name in self.tags and (self.tags[tag_name].divisor or 1) > 1:
            digits = 1
        key: str = self.key("number", tag_name, float(x), float(y))
        gid: str = uid("VAL_", key)
        weight: str = ' font-weight="bold"' if bold else ""
        self.svg.append(
            f'<g id="{gid}" type="svg-ext-value" fill="{color}" stroke="{color}" font-size="{size}"'
            f' stroke-width="0" font-family="{FONT}" text-anchor="{anchor}">'
            f'<text stroke-width="0" id="{uid("VAL_", key + "/text")}" fill="{color}" stroke="{color}"'
            f' font-size="{size}" font-family="{FONT}" text-anchor="{anchor}"{weight}'
            f' xml:space="preserve" x="{x:g}" y="{y:g}">##.##</text></g>'
        )
        self.item(gid, "svg-ext-value", "Value", tag_name, {
            "events": [],
            "variableId": self.tid(tag_name),
            "ranges": [{"type": OUTPUT_UNIT, "min": 0, "max": 0, "text": unit, "textId": "",
                        "fractionDigits": digits}],
        })

    def _semaphore(self, cx: float, cy: float, r: float, name: str, tag_name: str,
                   ranges: list[Json]) -> None:
        key: str = self.key("lamp", name, float(cx), float(cy))
        gid: str = uid("GSE_", key)
        self.svg.append(
            f'<g id="{gid}" type="svg-ext-gauge_semaphore" stroke="{INK}" stroke-width="1.5">'
            f'<circle id="{uid("GSE_", key + "/c")}" cx="{cx:g}" cy="{cy:g}" r="{r:g}"'
            f' fill="{WHITE}" stroke="{INK}"/></g>'
        )
        self.item(gid, "svg-ext-gauge_semaphore", "HtmlSemaphore", name,
                  {"events": [], "variableId": self.tid(tag_name), "ranges": ranges})

    def state_circle(self, cx: float, cy: float, r: float, tag_name: str, on: str = LIVE,
                     off: str = WHITE, above: float = 0.5) -> None:
        """A disc that is filled while a bit is 1 (or a value is above ``above``)
        and hollow otherwise."""
        self._semaphore(cx, cy, r, tag_name, tag_name, [
            {"min": -FAR, "max": above, "color": off, "stroke": INK},
            {"min": above + 1.0e-6, "max": FAR, "color": on, "stroke": INK},
        ])

    def step_lamp(self, cx: float, cy: float, tag_name: str, value: int, r: float = 6) -> None:
        """A disc that is filled while a tag holds ``value`` (a stage, a state)."""
        hollow: Json = {"color": WHITE, "stroke": INK}
        self._semaphore(cx, cy, r, f"{tag_name}={value}", tag_name, [
            {"min": -FAR, "max": value - 0.5, **hollow},
            {"min": value, "max": value, "color": LIVE, "stroke": INK},
            {"min": value + 0.5, "max": FAR, **hollow},
        ])

    def word(self, x: float, y: float, tag_name: str, words: dict[int, str], size: int = 13,
             anchor: str = "start", bold: bool = True, color: str = INK) -> None:
        """A state as a word: the tag's number shown as the text it stands for.
        One hidden text for each state, shown while the tag holds its number."""
        tag_id: str = self.tid(tag_name)
        for value, text in words.items():
            attrs: Json = {"x": f"{x:g}", "y": f"{y:g}", "font-size": size, "font-family": FONT,
                           "text-anchor": anchor, "fill": color, "stroke-width": 0}
            if bold:
                attrs["font-weight"] = "bold"
            self.shape("text", attrs, f"{tag_name}={value} {text}",
                       actions=shown(tag_id, value, value), content=text, hide=True)
        weight: str = ' font-weight="bold"' if bold else ""
        self.mock.append(
            f'<text x="{x:g}" y="{y:g}" font-size="{size}" font-family="{FONT}" text-anchor="{anchor}"'
            f' fill="{color}"{weight}>{next(iter(words.values()))}</text>'
        )

    # -- fills and scales --------------------------------------------------------------

    def _clip(self, d: str) -> str:
        cid: str = uid("clip_", self.key("clip", d))
        self.defs.append(f'<clipPath id="{cid}"><path d="{d}"/></clipPath>')
        return cid

    def fill(self, outline: str, x: float, y: float, w: float, h: float, tag_name: str,
             lo: float, hi: float, color: str, horizontal: bool = False,
             tint: tuple[str, list[tuple[float, float, str]]] | None = None) -> None:
        """What a vessel holds: a block of ``color`` that rises behind the
        ``outline`` as the tag goes from ``lo`` to ``hi`` (the box x, y, w, h is
        the span it moves through). ``horizontal`` grows it from the left.
        ``tint`` recolors it from a second tag: (tag, [(lo, hi, color)])."""
        cid: str = self._clip(outline)
        tag_id: str = self.tid(tag_name)
        if horizontal:
            start: Json = {"x": f"{x - w:g}", "y": f"{y:g}"}
            move: Json = action(tag_id, "moveByTags", 0, 0, axis="x", valueMin=lo, valueMax=hi,
                                positionMin=x - w, positionMax=x, duration=600)
            self.mock.append(f'<rect x="{x:g}" y="{y:g}" width="{w * 0.55:g}" height="{h:g}" fill="{color}"'
                             f' clip-path="url(#{cid})"/>')
        else:
            start = {"x": f"{x:g}", "y": f"{y + h:g}"}
            move = action(tag_id, "moveByTags", 0, 0, axis="y", valueMin=lo, valueMax=hi,
                          positionMin=y + h, positionMax=y, duration=600)
            self.mock.append(f'<rect x="{x:g}" y="{y + h * 0.45:g}" width="{w:g}" height="{h * 0.55:g}"'
                             f' fill="{color}" clip-path="url(#{cid})"/>')
        self.svg.append(f'<g clip-path="url(#{cid})">')
        self.shape(
            "rect", {**start, "width": f"{w:g}", "height": f"{h:g}", "fill": color, "stroke": "none"},
            f"{tag_name} fill", tag_name=tint[0] if tint else None,
            ranges=[{"min": a, "max": b, "color": c} for a, b, c in tint[1]] if tint else None,
            actions=[move],
        )
        self.svg.append("</g>")

    def level(self, x: float, y: float, w: float, h: float, tag_name: str, lo: float, hi: float,
              medium: str) -> None:
        """A sight glass: a narrow column that fills with the tag."""
        box: str = f"M{x:g} {y:g} h{w:g} v{h:g} h{-w:g} Z"
        self.rect(x, y, w, h, fill=WHITE, stroke=RULE, width=1)
        self.fill(box, x, y, w, h, tag_name, lo, hi, MEDIA[medium])
        self.rect(x, y, w, h, fill="none", stroke=DIM, width=1)

    def contents(self, outline: str, x: float, y: float, w: float, h: float, tag_name: str,
                 lo: float, hi: float, medium: str) -> None:
        """A vessel's contents in the vessel's own shape, then its outline again."""
        self.fill(outline, x, y, w, h, tag_name, lo, hi, MEDIA[medium])
        self.outline(outline)

    def limits(self, source: str) -> list[Alarm]:
        """The alarms of a measurement that have a fixed limit."""
        return [a for a in self.alarms.get(source, [])
                if a["kind"] in ("high", "highhigh", "low", "lowlow") and a.get("limit") is not None]

    def scale(self, x: float, y: float, w: float, h: float, tag_name: str, lo: float, hi: float,
              normal: tuple[float, float] | None = None, setpoint: str | None = None) -> None:
        """A measurement on a vertical scale: the normal band, each alarm limit as
        a mark, a hollow marker at the setpoint, and a pointer at the value."""
        span: float = hi - lo

        def at(value: float) -> float:
            return y + h * (1 - (min(max(value, lo), hi) - lo) / span)

        self.rect(x, y, w, h, fill=WHITE, stroke=RULE, width=1)
        if normal:
            self.rect(x, at(normal[1]), w, at(normal[0]) - at(normal[1]), fill=BAND, stroke="none", width=0)
        self.rect(x, y, w, h, fill="none", stroke=DIM, width=1)
        for alarm in self.limits(tag_name):
            limit: float = float(alarm["limit"])
            if lo <= limit <= hi:
                self.path(f"M{x - 4:g} {at(limit):g} L{x + w:g} {at(limit):g}", stroke=INK, width=1.5)
        if setpoint:
            self.shape(
                "path", {"d": f"M{x - 8:g} {y + h - 4:g} l6 4 l-6 4 Z", "fill": WHITE, "stroke": INK,
                         "stroke-width": 1},
                f"{setpoint} mark",
                actions=[action(self.tid(setpoint), "moveByTags", 0, 0, axis="y", valueMin=lo, valueMax=hi,
                                positionMin=y + h - 4, positionMax=y - 4, duration=600)],
            )
        self.shape(
            "path", {"d": f"M{x - 2:g} {y + h:g} L{x + w + 2:g} {y + h:g} l7 -5 l0 10 l-7 -5 Z",
                     "fill": INK, "stroke": INK, "stroke-width": 1.5, "stroke-linejoin": "round"},
            f"{tag_name} pointer",
            actions=[action(self.tid(tag_name), "moveByTags", 0, 0, axis="y", valueMin=lo, valueMax=hi,
                            positionMin=y + h - 5, positionMax=y - 5, duration=600)],
        )
        mid: float = y + h * 0.42
        self.mock.append(f'<path d="M{x - 2:g} {mid:g} L{x + w + 2:g} {mid:g} l7 -5 l0 10 l-7 -5 Z" fill="{INK}"'
                         f' stroke="{INK}" stroke-width="1.5" opacity="0.5"/>')

    def gauge(self, x: float, y: float, w: float, h: float, tag_name: str, lo: float, hi: float,
              normal: tuple[float, float] | None = None, digits: int = 1, unit: str = "",
              caption: str = "", setpoint: str | None = None) -> None:
        """A scale with its caption above, its value and unit below, and the
        measurement's alarm flags beside it."""
        if caption:
            self.label(x + w / 2, y - 8, caption, size=10, anchor="middle")
        self.scale(x, y, w, h, tag_name, lo, hi, normal=normal, setpoint=setpoint)
        self.number(x + w / 2 + 12, y + h + 16, tag_name, digits=digits, size=12)
        if unit:
            self.label(x + w / 2 + 14, y + h + 16, unit, size=9)
        self.flags(x + w + 14, y, tag_name, down=True)

    def hbar(self, x: float, y: float, w: float, h: float, tag_name: str, lo: float, hi: float,
             normal: tuple[float, float] | None = None, color: str = INK) -> None:
        """A quantity as a horizontal fill: the normal band above the bar, the
        alarm limits as ticks."""
        span: float = hi - lo
        if normal:
            self.rect(x + w * (normal[0] - lo) / span, y - 5, w * (normal[1] - normal[0]) / span, 4,
                      fill=BAND, stroke="none", width=0)
        self.rect(x, y, w, h, fill=WHITE, stroke=RULE, width=1)
        self.fill(f"M{x:g} {y:g} h{w:g} v{h:g} h{-w:g} Z", x, y, w, h, tag_name, lo, hi, color, horizontal=True)
        self.rect(x, y, w, h, fill="none", stroke=DIM, width=1)
        for alarm in self.limits(tag_name):
            limit: float = float(alarm["limit"])
            if lo <= limit <= hi:
                mx: float = x + w * (limit - lo) / span
                self.path(f"M{mx:g} {y - 6:g} L{mx:g} {y + h + 3:g}", stroke=INK, width=1.5)

    # -- alarms ------------------------------------------------------------------------

    def flag(self, x: float, y: float, alarm: Alarm) -> None:
        """An alarm flag at (x, y): hidden until its alarm is active. The alarm's
        text is the flag's tooltip."""
        if alarm["fuxa_tag_id"] not in {t.id for t in self.tags.values()}:
            return
        color, digit, ink = ALARM_LEVELS[alarm["priority"]]
        s: int = FLAG
        outline: str = {
            1: f"M{x:g} {y:g} h{s} v{s} h{-s} Z",
            2: f"M{x + s / 2:g} {y:g} L{x + s:g} {y + s:g} L{x:g} {y + s:g} Z",
            3: f"M{x:g} {y:g} L{x + s:g} {y:g} L{x + s / 2:g} {y + s:g} Z",
            4: f"M{x + s / 2:g} {y:g} L{x + s:g} {y + s / 2:g} L{x + s / 2:g} {y + s:g} L{x:g} {y + s / 2:g} Z",
        }[digit]
        text_y: float = y + {1: 11, 2: 13, 3: 9, 4: 10.5}[digit]
        size: int = {1: 11, 2: 9, 3: 9, 4: 9}[digit]
        body: str = (
            f"<title>{alarm['text']}</title>"
            f'<path d="{outline}" fill="{color}" stroke="{INK}" stroke-width="1" stroke-linejoin="round"/>'
            f'<text x="{x + s / 2:g}" y="{text_y:g}" font-size="{size}" font-family="{FONT}" font-weight="bold"'
            f' text-anchor="middle" fill="{ink}" stroke-width="0">{digit}</text>'
        )
        tag_id: str = alarm["fuxa_tag_id"]
        if alarm.get("evaluator") != "fuxa":
            lo, hi = 1.0, 1.0  # a PLC alarm bit
        elif alarm["kind"] in ("high", "highhigh"):
            lo, hi = float(alarm["limit"]), FAR
        elif alarm["kind"] in ("low", "lowlow"):
            lo, hi = -FAR, float(alarm["limit"])
        else:
            lo = hi = float(alarm["limit"])
        gid: str = uid("svg_", self.key("flag", alarm["name"], float(x), float(y)))
        self.svg.append(f'<g id="{gid}">{body}</g>')
        self.item(gid, "svg-ext-shapes-g", "Shapes", alarm["name"],
                  {"events": [], "actions": shown(tag_id, lo, hi), "ranges": []}, hide=True)
        self.mock.append(f'<g opacity="0.35">{body}</g>')

    def flags(self, x: float, y: float, source: str, step: float = FLAG + 3, down: bool = False) -> None:
        """A flag for every alarm of a measurement, side by side from (x, y)
        (``down``: one under the other), the most urgent first."""
        order: list[str] = list(ALARM_LEVELS)
        alarms: list[Alarm] = sorted(self.alarms.get(source, []), key=lambda a: order.index(a["priority"]))
        for i, alarm in enumerate(alarms):
            self.flag(x + (0 if down else i * step), y + (i * step if down else 0), alarm)

    # -- equipment ---------------------------------------------------------------------

    def _on_off(self, tag_name: str, on: str, off: str, above: float = 0.5) -> list[Json]:
        del tag_name
        return [{"min": -FAR, "max": above, "color": off}, {"min": above + 1.0e-6, "max": FAR, "color": on}]

    def pump(self, cx: float, cy: float, tag_name: str, r: float = 11, facing: str = "right") -> None:
        """A pump: a circle with its discharge triangle, filled while it runs."""
        self.state_circle(cx, cy, r, tag_name)
        t: float = r * 0.55
        d: str = (f"M{cx - t * 0.6:g} {cy - t:g} L{cx + t:g} {cy:g} L{cx - t * 0.6:g} {cy + t:g} Z"
                  if facing == "right" else
                  f"M{cx + t * 0.6:g} {cy - t:g} L{cx - t:g} {cy:g} L{cx + t * 0.6:g} {cy + t:g} Z")
        self.path(d, fill=WHITE, stroke=INK, width=1)

    def valve(self, cx: float, cy: float, tag_name: str, size: float = 8, vertical: bool = False,
              above: float = 0.5) -> None:
        """A valve: a bow tie on the line, filled while it is open (a control
        valve: while its output is above ``above``)."""
        s = size
        d: str = (f"M{cx - s:g} {cy - s * 0.7:g} L{cx + s:g} {cy + s * 0.7:g} L{cx + s:g} {cy - s * 0.7:g}"
                  f" L{cx - s:g} {cy + s * 0.7:g} Z"
                  if not vertical else
                  f"M{cx - s * 0.7:g} {cy - s:g} L{cx + s * 0.7:g} {cy + s:g} L{cx - s * 0.7:g} {cy + s:g}"
                  f" L{cx + s * 0.7:g} {cy - s:g} Z")
        self.shape("path", {"d": d, "fill": WHITE, "stroke": INK, "stroke-width": 1.5, "stroke-linejoin": "round"},
                   f"{tag_name} valve", tag_name=tag_name, ranges=self._on_off(tag_name, LIVE, WHITE, above))

    def fan(self, cx: float, cy: float, tag_name: str, r: float = 13, above: float = 0.5) -> None:
        """A fan: a disc that is filled while it runs, with blades that turn."""
        self.state_circle(cx, cy, r, tag_name, above=above)
        b: float = r * 0.78
        blades: str = "".join(
            f'<path d="M{cx:g} {cy:g} L{cx + dx:g} {cy + dy:g}" stroke="{WHITE}" stroke-width="4" stroke-linecap="round"/>'
            f'<path d="M{cx:g} {cy:g} L{cx + dx:g} {cy + dy:g}" stroke="{INK}" stroke-width="1.5" stroke-linecap="round"/>'
            for dx, dy in ((0.0, -b), (b * 0.866, b / 2), (-b * 0.866, b / 2))
        )
        # the unseen circle centers the group's box on the hub, which FUXA turns it about
        body: str = f'<circle cx="{cx:g}" cy="{cy:g}" r="{b:g}" fill="none" stroke="none"/>{blades}'
        gid: str = uid("svg_", self.key("fan", tag_name, float(cx), float(cy)))
        self.svg.append(f'<g id="{gid}">{body}</g>')
        tag_id: str = self.tid(tag_name)
        self.item(gid, "svg-ext-shapes-g", "Shapes", f"{tag_name} blades", {
            "events": [], "ranges": [],
            "actions": [action(tag_id, "clockwise", above + 1.0e-6, FAR), action(tag_id, "stop", -FAR, above)],
        })

    def switch(self, cx: float, cy: float, tag_name: str, r: float = 8) -> None:
        """A breaker or a contact on a line: a filled square while it is closed."""
        self.shape("rect", {"x": f"{cx - r:g}", "y": f"{cy - r:g}", "width": f"{2 * r:g}", "height": f"{2 * r:g}",
                            "fill": WHITE, "stroke": INK, "stroke-width": 1.5},
                   f"{tag_name} breaker", tag_name=tag_name, ranges=self._on_off(tag_name, MEDIA["power"], WHITE))

    def wire(self, points: list[tuple[float, float]], tag_name: str | None = None, width: float = 4) -> None:
        """A conductor: dark while the tag says it is live, pale while it is not."""
        d: str = "M" + " L".join(f"{x:g} {y:g}" for x, y in points)
        if tag_name is None:
            self.path(d, stroke=MEDIA["power"], width=width)
            return
        self.shape("path", {"d": d, "fill": "none", "stroke": MEDIA["power"], "stroke-width": width,
                            "stroke-linecap": "round", "stroke-linejoin": "round"},
                   f"{tag_name} wire", tag_name=tag_name,
                   ranges=[{"min": -FAR, "max": 0.5, "stroke": RULE}, {"min": 0.5 + 1.0e-6, "max": FAR, "stroke": MEDIA["power"]}])

    def pipe_run(self, points: list[tuple[float, float]], medium: str, width: float = 5,
                 flow: str | None = None, above: float = 0.5) -> None:
        """A pipe as a polyline in its medium's color, with a dark casing. With
        ``flow`` (a tag: a pump's run bit, a valve, a flow meter) the pipe shows
        the flow moving, from the first point to the last, while the tag is
        above ``above``."""
        d: str = "M" + " L".join(f"{x:g} {y:g}" for x, y in points)
        if flow is None:
            self.path(d, stroke=INK, width=width + 2)
            self.path(d, stroke=MEDIA[medium], width=width)
            return
        key: str = self.key("pipe", flow, d)
        gid: str = uid("PIE_", key)
        dash: float = max(width * 1.6, 6)
        line: str = 'fill="none" stroke-linejoin="round"'
        self.svg.append(
            f'<g id="{gid}" type="svg-ext-pipe" xml:space="preserve">'
            f'<path id="{uid("bPIE_", key + "/b")}" d="{d}" stroke-width="{width + 2:g}" stroke="{INK}" {line}/>'
            f'<path id="{uid("pPIE_", key + "/p")}" d="{d}" stroke-width="{width:g}" stroke="{MEDIA[medium]}" {line}/>'
            f'<path id="{uid("cPIE_", key + "/c")}" d="{d}" stroke-width="{max(width - 3, 1.5):g}" stroke="{WHITE}"'
            f' stroke-opacity="0.85" {line} stroke-dasharray="{dash:g} {dash * 1.6:g}" style="display:none"/></g>'
        )
        tag_id: str = self.tid(flow)
        self.item(gid, "svg-ext-pipe", "Pipe", f"{flow} flow", {
            "events": [],
            "actions": [action(tag_id, "clockwise", above + 1.0e-6, FAR), action(tag_id, "hidecontent", -FAR, above)],
            "options": {"border": INK, "borderWidth": width + 2, "pipe": MEDIA[medium], "pipeWidth": width,
                        "content": WHITE, "contentWidth": max(width - 3, 1.5), "contentSpace": dash},
        })

    # -- controls ----------------------------------------------------------------------

    def button(self, x: float, y: float, w: float, h: float, text: str, tag_name: str, value: int) -> None:  # type: ignore[override]
        """A button that writes ``value`` to a tag (see ``ViewBuilder.button``
        for why the group is written the way a saved button is)."""
        self.writable(tag_name)
        key: str = self.key("button", tag_name, value)
        gid: str = uid("HXB_", key)
        style: str = (
            "width:calc(100% - 6px);height:calc(100% - 6px);text-align:center;"
            f"background-color:{WHITE};color:{INK};border:1px solid {DIM};border-radius:3px;"
            f"font-size:13px;font-family:{FONT};"
        )
        self.svg.append(
            f'<g id="{gid}" type="svg-ext-html_button"'
            f' fill="rgba(0,0,0,0)" stroke="rgba(0,0,0,0)" xml:space="preserve">'
            f'<rect id="{uid("svg_", key + "/rect")}" x="{x:g}" y="{y:g}" width="{w:g}"'
            f' height="{h:g}" stroke-width="0"/>'
            f'<foreignObject id="H-{uid("HXB_", key + "/fo")}" x="{x:g}" y="{y:g}"'
            f' width="{w:g}" height="{h:g}"><button id="B-{uid("HXB_", key + "/b")}"'
            f' class="md-btn  md-btn-raised" style="{style}">{text}</button></foreignObject></g>'
        )
        self.item(gid, "svg-ext-html_button", "HtmlButton", text, {
            "events": [], "actions": [], "variableId": self.tid(tag_name), "text": text,
            "buttonAction": {"mode": "set", "setValue": str(value)},
        })

    def entry(self, x: float, y: float, tag_name: str, lo: float, hi: float, w: float = 56,
              h: float = 22, digits: int = 1) -> None:
        """A box that shows a setpoint and writes what the operator enters."""
        self.writable(tag_name)
        key: str = self.key("entry", tag_name, float(x), float(y))
        gid: str = uid("HXI_", key)
        self.svg.append(
            f'<g id="{gid}" stroke="{DIM}" text-anchor="right" font-family="{FONT}"'
            f' font-size="12" fill="#f1f1f1ff" type="svg-ext-html_input">'
            f'<rect id="{uid("svg_", key + "/rect")}" stroke="{DIM}" fill="{WHITE}"'
            f' height="{h:g}" width="{w:g}" y="{y:g}" x="{x:g}" stroke-width="1"/>'
            f'<foreignObject id="H-{uid("HXI_", key + "/fo")}" width="{w:g}" height="{h:g}"'
            f' y="{y:g}" x="{x:g}"><INPUT id="I-{uid("HXI_", key + "/in")}"'
            f' style="width: calc(100% - 7px); height: calc(100% - 7px); text-align: right;'
            f' border: unset; background-color: rgb(255, 255, 255); color: rgb(47, 54, 64); font-size: 12px;'
            f' vector-effect: non-scaling-stroke;" type="text"/></foreignObject></g>'
        )
        self.item(gid, "svg-ext-html_input", "HtmlInput", tag_name, {
            "events": [],
            "variableId": self.tid(tag_name),
            "ranges": [{"type": OUTPUT_UNIT, "min": 0, "max": 0, "text": "", "textId": "",
                        "fractionDigits": digits}],
            "options": {"updated": True, "numeric": True, "min": lo, "max": hi, "type": "number",
                        "selectOnClick": True},
        })

    def link(self, x: float, y: float, w: float, h: float, view_id: str, name: str) -> None:
        """A region that opens another view when it is clicked. Draw it after
        what it covers: it lies on top."""
        self.shape(
            "rect", {"x": f"{x:g}", "y": f"{y:g}", "width": f"{w:g}", "height": f"{h:g}", "rx": 6,
                     "fill": WHITE, "fill-opacity": 0, "stroke": "none", "style": "cursor:pointer"},
            f"open {name}",
            events=[{"type": "click", "action": "onpage", "actparam": view_id,
                     "actoptions": {"variablesMapping": []}}],
        )

    # -- trends and dials --------------------------------------------------------------

    def trend(self, x: float, y: float, w: float, h: float, heading: str,
              series: list[tuple[str, str, str]], y_range: tuple[float, float] | None = None) -> None:
        """A trend of a few values over the last hour: (tag, legend, color). It
        starts with what FUXA has stored (it stores every analog process value
        once a minute) and then follows the live values."""
        self.card(x, y, w, h, heading)
        # the legend, drawn here (FUXA's own takes a third of a small chart)
        lx: float = x + w - 12
        for _tag, legend, color in reversed(series):
            self.label(lx, y + 17, legend, size=10, color=INK, anchor="end")
            lx -= len(legend) * 5.8 + 5
            self.path(f"M{lx - 12:g} {y + 13:g} L{lx:g} {y + 13:g}", stroke=color, width=3)
            lx -= 24
        key: str = self.key("trend", heading)
        chart_id: str = uid("c_", key)
        self.charts.append({
            "id": chart_id,
            "name": f"{self.name}: {heading}",
            "lines": [{"id": self.tid(tag), "name": tag, "label": legend, "device": self.devices.get(tag, ""),
                       "color": color, "yaxis": 1, "lineWidth": 2} for tag, legend, color in series],
        })
        # FUXA keeps 40 px under the plot for its own legend, which is off here
        cx, cy, cw, ch = x + 4, y + 22, w - 8, h - 26 + 40
        options: Json = {
            "title": "", "fontFamily": "Roboto-Regular", "legendFontSize": 11, "titleHeight": 0,
            "colorBackground": "rgba(255,255,255,0)", "legendBackground": "rgba(255,255,255,0)",
            "axisLabelFontSize": 11, "labelsDivWidth": 0, "axisLineColor": DIM, "axisLabelColor": DIM,
            "gridLineColor": "#E3E6EA", "legendMode": "follow", "axisLabelX": " ", "series": [], "width": int(cw),
            "height": int(ch), "decimalsPrecision": 1, "realtime": TREND_MINUTES,
            "mouseWheelScroll": False, "mouseWheelZoom": False, "staticChart": False,
            "dateFormat": "MM_DD_YYYY", "timeFormat": "HH_mm_ss", "loadOldValues": True,
            "lastRange": "last1h",
        }
        if y_range:
            options["scaleY1min"], options["scaleY1max"] = y_range
        gid: str = uid("HXC_", key)
        self.svg.append(
            f'<g id="{gid}" type="svg-ext-html_chart" fill="{WHITE}" stroke="#000000" stroke-width="1"'
            f' font-size="14" font-family="{FONT}" text-anchor="right" xml:space="preserve">'
            f'<rect id="{uid("svg_", key + "/r")}" x="{cx:g}" y="{cy:g}" width="{cw:g}" height="{ch:g}"'
            f' stroke-width="0" fill-opacity="0"/>'
            f'<foreignObject id="H-{uid("HXC_", key + "/h")}" x="{cx:g}" y="{cy:g}" width="{cw:g}" height="{ch:g}">'
            f'<div id="D-{uid("HXC_", key + "/d")}" style="width:100%;height:100%;"></div></foreignObject></g>'
        )
        self.item(gid, "svg-ext-html_chart", "HtmlChart", heading,
                  {"id": chart_id, "type": "realtime1", "options": options, "events": []})
        for i in range(1, 4):
            gy: float = y + 24 + (h - 34) * i / 4
            self.mock.append(f'<path d="M{x + 10:g} {gy:g} L{x + w - 10:g} {gy:g}" stroke="{RULE}" stroke-width="0.5"/>')

    def dial(self, x: float, y: float, w: float, h: float, tag_name: str, lo: float, hi: float,
             zones: list[tuple[float, float, str]] | None = None, digits: int = 0) -> None:
        """An arc gauge for one headline figure (FUXA draws it on a canvas)."""
        key: str = self.key("dial", tag_name)
        gid: str = uid("BAG_", key)
        self.svg.append(
            f'<g id="{gid}" type="svg-ext-html_bag" fill="{WHITE}" stroke="#000000" stroke-width="1"'
            f' font-size="14" font-family="{FONT}" text-anchor="right" xml:space="preserve">'
            f'<rect id="{uid("svg_", key + "/r")}" x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}"'
            f' stroke-width="0" fill-opacity="0"/>'
            f'<foreignObject id="H-{uid("BAG_", key + "/h")}" x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}">'
            f'<div id="D-{uid("BAG_", key + "/d")}" style="width:100%;height:100%;"></div></foreignObject></g>'
        )
        options: Json = {
            "type": 1, "minValue": lo, "maxValue": hi, "animationSpeed": 40,
            "colorStart": LIVE, "colorStop": LIVE, "gradientType": "", "strokeColor": BODY,
            "pointer": {"length": 0.5, "strokeWidth": 0.035, "iconScale": 1.0, "color": "rgba(0,0,0,0)"},
            "angle": 0.15, "lineWidth": 0.14, "radiusScale": 0.9, "fontSize": 20, "fontFamily": "Sans-serif",
            "textFilePosition": 30, "limitMax": False, "limitMin": False, "highDpiSupport": True,
            "backgroundColor": "rgba(255, 255, 255, 0)", "shadowColor": "#d5d5d5", "fractionDigits": digits,
            "ticksEnabled": False,
            "renderTicks": {"divisions": 0, "divWidth": 0, "divLength": 0, "subDivisions": 0, "subWidth": 0,
                            "subLength": 0},
            "staticFontSize": 10, "staticFontColor": INK,
        }
        if zones:
            options["staticZones"] = [{"strokeStyle": c, "min": a, "max": b} for a, b, c in zones]
        self.item(gid, "svg-ext-html_bag", "HtmlBag", tag_name,
                  {"events": [], "actions": [], "variableId": self.tid(tag_name), "ranges": [], "options": options})
        self.mock.append(
            f'<path d="M{x + 8:g} {y + h * 0.7:g} A{w / 2 - 8:g} {w / 2 - 8:g} 0 0 1 {x + w - 8:g} {y + h * 0.7:g}"'
            f' fill="none" stroke="{LIVE}" stroke-width="9"/>'
        )

    # -- the result --------------------------------------------------------------------

    def svg_content(self) -> str:
        defs: str = f"<defs>{''.join(self.defs)}</defs>" if self.defs else ""
        body: str = "\n  ".join(self.svg)
        return (
            f'<svg width="{self.width}" height="{self.height}"'
            f' xmlns="http://www.w3.org/2000/svg" xmlns:svg="http://www.w3.org/2000/svg">{defs}'
            f"\n <g>\n  <title>Layer 1</title>\n  {body}\n </g>\n</svg>"
        )

    def screen(self, view_id: str) -> Screen:
        items: Json = {}
        for gid, gauge in self.items.items():
            items[gid] = gauge.model_dump()
            if gid in self.hidden:
                items[gid]["hide"] = True
        view: Json = {
            "id": view_id,
            "name": self.name,
            "profile": {
                "width": self.width,
                "height": self.height,
                "bkcolor": BRAND.process_bkcolor(),
                "margin": 10,
                "align": "topCenter",
                "gridType": "fixed",
                "viewRenderDelay": 0,
            },
            "items": items,
            "variables": {},
            "svgcontent": self.svg_content(),
            "type": "svg",
        }
        return Screen(view, self.charts)
