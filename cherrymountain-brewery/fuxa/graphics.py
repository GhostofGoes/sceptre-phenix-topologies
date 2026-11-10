"""Process graphics for the FUXA projects: the drawing kit.

The generated tag lists ("... points" views) show every value as a number. An
operator needs the plant instead: vessels that show what is in them, bars that
show where a value sits in its range, equipment that shows whether it runs,
pipes that show where product goes. This module is the kit those graphics are
drawn with, and ``views.py`` lays the screens out with it.

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
  is on is filled with one "live" color and says so in a word; one that is off
  is an outline.
* **A number comes with its range.** A measurement that has limits is drawn as
  a bar with the normal band behind it, and its setpoint as a mark on the bar.
* **Flow reads left to right**, product on top and utilities below, and every
  screen keeps a vessel where the overview put it.
"""

from __future__ import annotations

from typing import Any, Final

from build_project import BRAND, OUTPUT_UNIT, Gauge, PlantTag, ViewBuilder, uid

# ---------------------------------------------------------------- palette

#: The picture's own grays (none is a brand accent or an alarm color).
INK: Final[str] = "#2F3640"  # outlines of equipment, primary text
DIM: Final[str] = "#6B7280"  # secondary text, units, captions
RULE: Final[str] = "#B8BEC7"  # scales, quiet separators
BODY: Final[str] = "#DDE1E6"  # the body of a vessel or a machine
CARD: Final[str] = "#FBFBFC"  # a panel that groups values
BAND: Final[str] = "#C5D3DE"  # the normal band of an analog bar
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
#: Alarm priority -> (color, the shape drawn for it). The colors are the brand's
#: reserved four; the shape and the number make a flag readable without them.
ALARM_LEVELS: Final[dict[str, tuple[str, int]]] = {
    "critical": (BRAND.reserved_for_alarms["critical"], 1),
    "high": (BRAND.reserved_for_alarms["high"], 2),
    "medium": (BRAND.reserved_for_alarms["medium"], 3),
    "low": (BRAND.reserved_for_alarms["low"], 4),
}
FONT: Final[str] = "sans-serif"


class Canvas(ViewBuilder):
    """A view under construction: static drawing, then the live elements."""

    def __init__(self, tags: dict[str, PlantTag], width: int, height: int,
                 internal: dict[str, str] | None = None) -> None:
        super().__init__(tags, width, height, internal)
        #: Live elements whose FUXA form is still to be written out.
        self.pending: list[tuple[str, tuple[Any, ...]]] = []

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

    def pipe_run(self, points: list[tuple[float, float]], medium: str, width: float = 5) -> None:
        """A pipe as a polyline in its medium's color, with a dark casing."""
        d: str = "M" + " L".join(f"{x:g} {y:g}" for x, y in points)
        self.path(d, stroke=INK, width=width + 2)
        self.path(d, stroke=MEDIA[medium], width=width)

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

    def unitank(self, x: float, y: float, w: float, h: float, cone: float) -> None:
        """A cylindroconical vessel: dished head, straight side, cone, legs."""
        cx: float = x + w / 2
        for leg in (x + 8, x + w - 8):
            self.path(f"M{leg:g} {y + h - 4:g} L{leg:g} {y + h + cone + 16:g}", stroke=DIM, width=3)
        self.path(
            f"M{x:g} {y + 14:g} Q{cx:g} {y - 12:g} {x + w:g} {y + 14:g} L{x + w:g} {y + h:g}"
            f" L{cx + 7:g} {y + h + cone:g} L{cx - 7:g} {y + h + cone:g} L{x:g} {y + h:g} Z",
            fill=BODY,
        )

    def tank(self, x: float, y: float, w: float, h: float) -> None:
        """A vertical vessel with dished ends."""
        cx: float = x + w / 2
        self.path(
            f"M{x:g} {y + 12:g} Q{cx:g} {y - 10:g} {x + w:g} {y + 12:g} L{x + w:g} {y + h - 12:g}"
            f" Q{cx:g} {y + h + 10:g} {x:g} {y + h - 12:g} Z",
            fill=BODY,
        )

    # -- live elements (bound to tags) -------------------------------------------------

    def number(self, x: float, y: float, tag_name: str, digits: int = 1, unit: str = "",
               size: int = 14, anchor: str = "end", bold: bool = True) -> None:
        """A measured value, right-aligned so a column of them lines up."""
        tag_id: str = self._tag_id(tag_name)
        key: str = f"{tag_name}@{x:g},{y:g}"
        gid: str = uid("VAL_", key)
        weight: str = ' font-weight="bold"' if bold else ""
        self.svg.append(
            f'<g id="{gid}" type="svg-ext-value" fill="{INK}" stroke="{INK}" font-size="{size}"'
            f' stroke-width="0" font-family="{FONT}" text-anchor="{anchor}">'
            f'<text stroke-width="0" id="{uid("VAL_", key + "/text")}" fill="{INK}" stroke="{INK}"'
            f' font-size="{size}" font-family="{FONT}" text-anchor="{anchor}"{weight}'
            f' xml:space="preserve" x="{x:g}" y="{y:g}">##.##</text></g>'
        )
        self.items[gid] = Gauge(
            id=gid,
            type="svg-ext-value",
            name=tag_name,
            label="Value",
            property={
                "events": [],
                "variableId": tag_id,
                "ranges": [{"type": OUTPUT_UNIT, "min": 0, "max": 0, "text": unit, "textId": "",
                            "fractionDigits": digits}],
            },
        )

    def level(self, x: float, y: float, w: float, h: float, tag_name: str, lo: float, hi: float,
              medium: str) -> None:
        """What a vessel holds, as a fill that rises with the tag (lo to hi)."""
        self.rect(x, y, w, h, fill="#FFFFFF", stroke=RULE, width=1)
        self._progress(x, y, w, h, tag_name, lo, hi, MEDIA[medium])

    def bar(self, x: float, y: float, w: float, h: float, tag_name: str, lo: float, hi: float,
            normal: tuple[float, float] | None = None, marks: tuple[float, ...] = ()) -> None:
        """A measurement in its range: the scale's frame, the normal band behind,
        the value as a fill, and limit marks (fixed values) at the side."""
        span: float = hi - lo
        if normal:
            # the band stands beside the column, so the fill never hides it
            top: float = y + h * (1 - (normal[1] - lo) / span)
            self.rect(x - 6, top, 5, h * (normal[1] - normal[0]) / span, fill=BAND, stroke="none", width=0)
        self.rect(x, y, w, h, fill="#FFFFFF", stroke=RULE, width=1)
        self._progress(x, y, w, h, tag_name, lo, hi, INK)
        for mark in marks:
            my: float = y + h * (1 - (mark - lo) / span)
            self.path(f"M{x - 7:g} {my:g} L{x + w + 4:g} {my:g}", stroke=INK, width=1.5)

    def _progress(self, x: float, y: float, w: float, h: float, tag_name: str, lo: float, hi: float,
                  color: str) -> None:
        """The moving fill of a level or a bar (bound in ``bind_progress``)."""
        self.pending.append(("progress", (x, y, w, h, tag_name, lo, hi, color)))

    def pump(self, cx: float, cy: float, tag_name: str, r: float = 11, facing: str = "right") -> None:
        """A pump: a circle with its discharge triangle, filled while it runs."""
        self.state_circle(cx, cy, r, tag_name)
        t: float = r * 0.55
        d: str = (f"M{cx - t * 0.6:g} {cy - t:g} L{cx + t:g} {cy:g} L{cx - t * 0.6:g} {cy + t:g} Z"
                  if facing == "right" else
                  f"M{cx + t * 0.6:g} {cy - t:g} L{cx - t:g} {cy:g} L{cx + t * 0.6:g} {cy + t:g} Z")
        self.path(d, fill="#FFFFFF", stroke=INK, width=1)

    def valve(self, cx: float, cy: float, tag_name: str, size: float = 9, vertical: bool = False) -> None:
        """A valve: a small disc on the line, filled while it is open."""
        s = size
        d: str = (f"M{cx - s:g} {cy - s:g} L{cx + s:g} {cy + s:g} L{cx + s:g} {cy - s:g} L{cx - s:g} {cy + s:g} Z"
                  if not vertical else
                  f"M{cx - s:g} {cy - s:g} L{cx + s:g} {cy + s:g} L{cx - s:g} {cy + s:g} L{cx + s:g} {cy - s:g} Z")
        self.path(d, fill="#FFFFFF", stroke=INK, width=1.5)
        self.state_circle(cx, cy, s * 0.55, tag_name)

    def state_circle(self, cx: float, cy: float, r: float, tag_name: str, on: str = LIVE,
                     off: str = "#FFFFFF") -> None:
        """A disc that is filled while a bit is 1 and hollow while it is 0."""
        tag_id: str = self._tag_id(tag_name)
        key: str = f"{tag_name}@{cx:g},{cy:g}"
        gid: str = uid("GSE_", key)
        self.svg.append(
            f'<g id="{gid}" type="svg-ext-gauge_semaphore" stroke="{INK}" stroke-width="1.5">'
            f'<circle id="{uid("GSE_", key + "/c")}" cx="{cx:g}" cy="{cy:g}" r="{r:g}"'
            f' fill="{off}" stroke="{INK}"/></g>'
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
                    {"min": 0, "max": 0, "color": off, "stroke": INK},
                    {"min": 1, "max": 1, "color": on, "stroke": INK},
                ],
            },
        )

    def step_lamp(self, cx: float, cy: float, tag_name: str, value: int, r: float = 6) -> None:
        """A disc that is filled while a tag holds ``value`` (a stage, a state)."""
        tag_id: str = self._tag_id(tag_name)
        key: str = f"{tag_name}={value}@{cx:g},{cy:g}"
        gid: str = uid("GSE_", key)
        self.svg.append(
            f'<g id="{gid}" type="svg-ext-gauge_semaphore" stroke="{INK}" stroke-width="1">'
            f'<circle id="{uid("GSE_", key + "/c")}" cx="{cx:g}" cy="{cy:g}" r="{r:g}"'
            f' fill="#FFFFFF" stroke="{INK}"/></g>'
        )
        hollow: dict[str, Any] = {"color": "#FFFFFF", "stroke": INK}
        ranges: list[dict[str, Any]] = [{"min": value, "max": value, "color": LIVE, "stroke": INK}]
        if value > 0:
            ranges.insert(0, {"min": 0, "max": value - 1, **hollow})
        ranges.append({"min": value + 1, "max": 9999, **hollow})
        self.items[gid] = Gauge(
            id=gid, type="svg-ext-gauge_semaphore", name=f"{tag_name}={value}", label="HtmlSemaphore",
            property={"events": [], "variableId": tag_id, "ranges": ranges},
        )

    def flag(self, x: float, y: float, tag_name: str, level: str) -> None:
        """An alarm flag: hidden while the alarm bit is 0 (bound in ``bind``)."""
        self.pending.append(("flag", (x, y, tag_name, level)))

    def has(self, *tag_names: str) -> bool:
        """True when the project has every one of these tags (a variant may not)."""
        return all(n in self.tags or n in self.internal for n in tag_names)

    def tag(self, name: str) -> PlantTag:
        return self.tags[name]

    def view(self, name: str, view_id: str) -> dict[str, Any]:
        return {
            "id": view_id,
            "name": name,
            "profile": {
                "width": self.width,
                "height": self.height,
                "bkcolor": BRAND.process_bkcolor(),
                "margin": 10,
                "align": "topCenter",
                "gridType": "fixed",
                "viewRenderDelay": 0,
            },
            "items": {k: g.model_dump() for k, g in self.items.items()},
            "variables": {},
            "svgcontent": self.svg_content(),
            "type": "svg",
        }
