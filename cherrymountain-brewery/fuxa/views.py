"""The process graphics of scada-fuxa and bas-fuxa, laid out with ``graphics.py``.

Each function draws one screen for the tags a project has. A screen is 1,240 px
wide at most, so it fits an HMI station's 1440x900 display beside the menu.
"""

from __future__ import annotations

from typing import Any

from build_project import PlantTag, uid
from graphics import DIM, INK, LIVE, MEDIA, RULE, Canvas

WIDTH: int = 1240
HEIGHT: int = 820
FVS: tuple[str, ...] = ("FV101", "FV102", "FV103", "FV104")
BBTS: tuple[str, ...] = ("BBT201", "BBT202")
#: 15 psig, the vessels' relief pressure, as the site's absolute pressure.
PSV_PSIA: float = 27.1


def cellar_view(tags: dict[str, PlantTag]) -> dict[str, Any]:
    """The cellar: the four fermenters and, where the plant has them, the two
    bright tanks, with beer moving left to right toward the filler."""
    c: Canvas = Canvas(tags, WIDTH, HEIGHT)
    plant: bool = c.has("CEL.BBT201.LT01.PV")
    c.title(20, 30, "Cellar")
    c.label(100, 30, "fermentation and conditioning", size=13)

    # The wort header from the brewhouse, over the fermenters.
    c.label(20, 62, "from brewhouse", size=10)
    c.pipe_run([(20, 72), (40 + 3 * 172 + 55, 72)], "wort")
    for i, fv in enumerate(FVS):
        x: float = 40 + i * 172
        _fermenter(c, x, 100, fv, i + 1, profile=c.has(f"CEL.PROFILE.KS0{i + 1}.STAGE"))

    if plant:
        # Racking: under the cones to the transfer pump, then up into BBT-201.
        c.pipe_run([(95, 440), (760, 440), (760, 300), (790, 300)], "beer")
        c.pump(742, 440, "CEL.XFER.YS01.PV")
        c.label(742, 468, "transfer", size=10, anchor="middle")
        c.number(742, 484, "CEL.XFER.FT01.PV", digits=0, unit="gal/min", size=11, anchor="middle", bold=False)
        for j, bbt in enumerate(BBTS):
            _bright_tank(c, 800 + j * 190, 110, bbt)
        # BBT-201 to BBT-202 (the crew's hose), and BBT-202 to the filler.
        c.pipe_run([(920, 330), (990, 330)], "beer")
        c.pipe_run([(1110, 330), (1215, 330)], "beer")
        c.valve(1150, 330, "CEL.BBT202.XV01.CMD")
        c.arrow(1222, 330)
        c.label(1215, 316, "to filler", size=10, anchor="end")

    return c.view("Cellar", uid("v_", "Cellar"))


def _fermenter(c: Canvas, x: float, y: float, fv: str, n: int, profile: bool) -> None:
    """One unitank at (x, y): the drop from the wort header, the jackets, the
    numbers on the vessel, the pressure and extract bars beside it, and the
    profile's stage under it."""
    w, h, cone = 110.0, 220.0, 70.0
    cx: float = x + w / 2
    t: str = f"CEL.{fv}"
    c.pipe_run([(cx, 72), (cx, y + 2)], "wort", width=4)
    c.unitank(x, y, w, h, cone)
    # glycol jackets: a band on the side and one on the cone, live while their valve is open
    c.rect(x - 7, y + 40, 6, 120, fill="#FFFFFF", stroke=INK, width=1)
    c.rect(x + w + 1, y + 40, 6, 120, fill="#FFFFFF", stroke=INK, width=1)
    c.state_circle(x - 4, y + 172, 5, f"{t}.TV01.CMD", on=MEDIA["glycol"])
    c.state_circle(cx - 30, y + h + 30, 5, f"{t}.TV02.CMD", on=MEDIA["glycol"])
    c.label(cx, y + 34, f"FV-{fv[2:]}", size=15, color=INK, anchor="middle", bold=True)
    c.number(cx + 28, y + 78, f"{t}.TT01.PV", digits=1, unit="", size=24)
    c.label(cx + 30, y + 78, "°F", size=12)
    c.label(cx - 38, y + 100, "set", size=10)
    c.number(cx + 28, y + 100, f"{t}.TT01.SP", digits=0, unit="", size=12, bold=False)
    c.label(cx - 38, y + 130, "cone", size=10)
    c.number(cx + 28, y + 130, f"{t}.TT02.PV", digits=1, unit="", size=12, bold=False)
    c.label(cx - 38, y + 160, "extract", size=10)
    c.number(cx + 28, y + 160, f"{t}.DT01.PV", digits=2, unit="", size=14)
    c.label(cx + 30, y + 160, "°P", size=10)
    # head pressure in its range, with the relief pressure marked
    bx: float = x + w + 22
    c.label(bx + 5, y + 30, "psia", size=9, anchor="middle")
    c.bar(bx, y + 38, 10, 150, f"{t}.PT01.PV", 12.0, 30.0, normal=(13.0, 26.1), marks=(PSV_PSIA,))
    c.number(bx + 16, y + 204, f"{t}.PT01.PV", digits=1, unit="", size=11, anchor="middle")
    if profile:
        base: float = y + h + cone + 40
        for k, word in enumerate(("ferment", "rest", "crash")):
            c.step_lamp(x + 8 + k * 46, base, f"CEL.PROFILE.KS0{n}.STAGE", k + 1)
            c.label(x + 17 + k * 46, base + 4, word, size=9)


def _bright_tank(c: Canvas, x: float, y: float, bbt: str) -> None:
    w, h = 120.0, 250.0
    cx: float = x + w / 2
    t: str = f"CEL.{bbt}"
    c.tank(x, y, w, h)
    c.level(x + 12, y + 44, 22, h - 80, f"{t}.LT01.PV", 0.0, 100.0, "beer")
    c.label(cx, y + 30, f"BBT-{bbt[3:]}", size=15, color=INK, anchor="middle", bold=True)
    c.number(x + w - 14, y + 80, f"{t}.LT01.PV", digits=0, unit="", size=22)
    c.label(x + w - 12, y + 80, "%", size=11)
    c.number(x + w - 14, y + 112, f"{t}.TT01.PV", digits=1, unit="", size=13)
    c.label(x + w - 12, y + 112, "°F", size=9)
    c.number(x + w - 14, y + 138, f"{t}.PT01.PV", digits=1, unit="", size=13)
    c.label(x + w - 12, y + 138, "psia", size=9)
    c.number(x + w - 14, y + 164, f"{t}.AT01.PV", digits=2, unit="", size=13)
    c.label(x + w - 12, y + 164, "vol", size=9)
