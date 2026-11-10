"""The process graphics of scada-fuxa and bas-fuxa, laid out with ``hmi_kit.py``.

Each function draws one screen. A screen is 1,240 px wide and 820 px high, so it
fits an HMI station's 1440x900 display beside the menu without scrolling.

The screens form the hierarchy ISA-101 describes. **Overview** is level 1: the
whole plant at a glance, with nothing to operate; each of its panels opens the
area behind it. **Brewhouse**, **Cellar**, **Packaging**, **Utilities**,
**Power**, **Cellar CO2**, and bas-fuxa's **Building** are level 2: the picture
an operator runs an area from. The generated "... points" tables stay as levels
3 and 4: every tag of a controller, for the engineer.
"""

from __future__ import annotations

from typing import NamedTuple

from hmi_base import PlantTag, uid
from hmi_kit import BAND, BODY, DIM, INK, LIVE, MEDIA, RULE, WHITE, Alarm, Canvas, Screen

WIDTH: int = 1240
HEIGHT: int = 820
FVS: tuple[str, ...] = ("FV101", "FV102", "FV103", "FV104")
BBTS: tuple[str, ...] = ("BBT201", "BBT202")
#: View name -> view id. FUXA opens a view by its name (the kiosks do) or its
#: id (a link does). The Cellar's id is the one it has always had.
VIEW_ID: dict[str, str] = {
    "Overview": uid("v_", "hmi/Overview"),
    "Brewhouse": uid("v_", "hmi/Brewhouse"),
    "Batch": uid("v_", "Batch"),
    "Cellar": uid("v_", "Cellar"),
    "Packaging": uid("v_", "hmi/Packaging"),
    "Utilities": uid("v_", "hmi/Utilities"),
    "Power": uid("v_", "hmi/Power"),
    "Cellar CO2": uid("v_", "hmi/Cellar CO2"),
    "Building": uid("v_", "hmi/Building"),
}
PACKML: dict[int, str] = {
    0: "Undefined", 1: "Clearing", 2: "Stopped", 3: "Starting", 4: "Idle", 5: "Suspended",
    6: "Execute", 7: "Stopping", 8: "Aborting", 9: "Aborted", 10: "Holding", 11: "Held",
    12: "Unholding", 13: "Suspending", 14: "Unsuspending", 15: "Resetting", 16: "Completing",
    17: "Complete",
}
PHASES: dict[int, str] = {
    0: "No phase", 1: "Mill", 2: "Heat liquor", 3: "Mash in", 4: "Mash step", 5: "Vorlauf",
    6: "Lauter", 7: "Boil", 8: "Whirlpool", 9: "Knockout", 10: "CIP ready",
}
PHASE_STATE: dict[int, str] = {
    0: "Idle", 1: "Running", 2: "Holding", 3: "Held", 4: "Restarting", 5: "Complete", 6: "Aborted",
}
STAGES: dict[int, str] = {0: "empty", 1: "fermenting", 2: "diacetyl rest", 3: "crash"}
CIP_STEPS: dict[int, str] = {
    0: "Idle", 1: "Pre-rinse", 2: "Caustic wash", 3: "Rinse", 4: "Acid wash", 5: "Rinse",
    6: "Sanitize", 7: "Done",
}
OCCUPANCY: dict[int, str] = {1: "Occupied", 2: "Unoccupied", 3: "Standby"}
#: What a fermenter holds, from its extract: nothing, then wort that turns to
#: beer as the extract falls (degrees Plato).
FERMENTING: list[tuple[float, float, str]] = [
    (-1.0e9, 0.5, BODY),
    (0.5001, 4.0, MEDIA["beer"]),
    (4.0001, 9.0, "#C99F45"),
    (9.0001, 1.0e9, MEDIA["wort"]),
]
#: The four lines of a trend of the four fermenters.
FV_COLORS: tuple[str, ...] = (INK, MEDIA["wort"], LIVE, MEDIA["cip"])


class Plant(NamedTuple):
    """What a screen is drawn from: a host's tags, the alarms by their source
    tag, and the device each tag is read from."""

    tags: dict[str, PlantTag]
    alarms: dict[str, list[Alarm]]
    devices: dict[str, str]


def canvas(name: str, plant: Plant) -> Canvas:
    return Canvas(name, plant.tags, WIDTH, HEIGHT, alarms=plant.alarms, devices=plant.devices)


def row(c: Canvas, x: float, y: float, caption: str, tag: str, digits: int = 1, unit: str = "",
        width: float = 150) -> None:
    """A caption and its value on one line, with the value's alarm flags after it."""
    if caption:
        c.label(x, y, caption, size=11)
    c.number(x + width - 26, y, tag, digits=digits, size=12)
    if unit:
        c.label(x + width - 23, y, unit, size=9)
    c.flags(x + width + 6 + len(unit) * 4.6, y - 11, tag)


def status(c: Canvas, x: float, y: float, caption: str, tag: str, r: float = 5) -> None:
    """A lamp and what it means while it is lit, then the state's alarm flag."""
    c.state_circle(x + r, y - 4, r, tag)
    c.label(x + 2 * r + 6, y, caption, size=11, color=INK)
    c.flags(x + 2 * r + 12 + len(caption) * 5.6, y - 11, tag)


def vessel(c: Canvas, x: float, y: float, w: float, h: float, tag: str, medium: str) -> None:
    """A tank whose contents rise and fall with its level, in the tank's shape."""
    c.tank(x, y, w, h)
    c.contents(c.tank_outline(x, y, w, h), x, y, w, h, tag, 0, 100, medium)


# ---------------------------------------------------------------- overview (level 1)


def overview(plant: Plant) -> Screen:
    """The plant at a glance: product moving left to right across the top, the
    services under it, life safety and the trends at the bottom."""
    c: Canvas = canvas("Overview", plant)
    c.title(20, 30, "Plant overview")
    c.label(172, 30, "product flows left to right; click a panel to open its area", size=12)

    # -- brewhouse
    c.card(20, 46, 390, 286, "Brewhouse")
    c.word(120, 63, "BH.BATCH.XC01.PHASE", PHASES, size=12)
    c.word(400, 63, "BH.BATCH.XS01.STATE", PHASE_STATE, size=12, anchor="end")
    c.pipe_run([(118, 232), (166, 232)], "hot", 4, flow="BH.MLT.FT02.PV", above=1)
    c.pipe_run([(240, 232), (288, 232)], "wort", 4, flow="BH.MLT.FT01.PV", above=1)
    c.pipe_run([(362, 232), (424, 232)], "wort", 4, flow="BH.KO.FT01.PV", above=1)
    for i, (unit, name, medium) in enumerate((("HLT", "Hot liquor", "hot"), ("MLT", "Mash tun", "wort"),
                                              ("KTL", "Kettle", "wort"))):
        x: float = 44 + i * 122
        vessel(c, x, 96, 74, 150, f"BH.{unit}.LT01.PV", medium)
        c.label(x + 37, 90, name, size=11, color=INK, anchor="middle", bold=True)
        c.number(x + 37, 150, f"BH.{unit}.TT01.PV", digits=0, unit="°F", size=15, anchor="middle")
        c.number(x + 37, 172, f"BH.{unit}.LT01.PV", digits=0, unit="%", size=12, anchor="middle", bold=False)
        c.flags(x + 80, 100, f"BH.{unit}.TT01.PV", down=True)
        c.flags(x + 80, 140, f"BH.{unit}.LT01.PV", down=True)
    row(c, 36, 288, "Knockout", "BH.KO.FT01.PV", 0, "gal/min", 150)
    row(c, 36, 312, "Steam", "UTL.STM.PT01.PV", 0, "psig", 150)
    row(c, 226, 288, "Boil time", "BH.KTL.KI01.PV", 0, "min", 150)
    status(c, 226, 312, "Boiling", "BH.KTL.XS01.BOIL")
    c.link(20, 46, 390, 286, VIEW_ID["Brewhouse"], "Brewhouse")
    c.arrow(428, 232)

    # -- cellar
    c.card(430, 46, 420, 286, "Cellar")
    for i, fv in enumerate(FVS):
        x = 462 + i * 66
        c.unitank(x, 96, 50, 96, 34)
        c.shape("path", {"d": c.unitank_outline(x, 96, 50, 96, 34), "fill": BODY, "stroke": INK, "stroke-width": 1.5},
                f"{fv} contents", tag_name=f"CEL.{fv}.DT01.PV",
                ranges=[{"min": a, "max": b, "color": color} for a, b, color in FERMENTING])
        c.label(x + 25, 90, fv[2:], size=11, color=INK, anchor="middle", bold=True)
        c.number(x + 25, 136, f"CEL.{fv}.TT01.PV", digits=1, size=14, anchor="middle")
        c.number(x + 25, 156, f"CEL.{fv}.DT01.PV", digits=1, size=11, anchor="middle", bold=False)
        c.word(x + 25, 252, f"CEL.PROFILE.KS0{i + 1}.STAGE",
               {0: "empty", 1: "ferment", 2: "rest", 3: "crash"}, size=10, anchor="middle", bold=False, color=DIM)
        c.flags(x + 1, 262, f"CEL.{fv}.TT01.PV")
        c.flags(x + 1, 280, f"CEL.{fv}.PT01.PV")
    for j, bbt in enumerate(BBTS):
        x = 730 + j * 60
        vessel(c, x, 96, 48, 134, f"CEL.{bbt}.LT01.PV", "beer")
        c.label(x + 24, 90, bbt[3:], size=11, color=INK, anchor="middle", bold=True)
        c.number(x + 24, 150, f"CEL.{bbt}.LT01.PV", digits=0, size=14, anchor="middle")
        c.number(x + 24, 170, f"CEL.{bbt}.AT01.PV", digits=1, size=11, anchor="middle", bold=False)
        c.flags(x + 8, 244, f"CEL.{bbt}.PT01.PV")
    c.pipe_run([(838, 200), (864, 200)], "beer", 4, flow="CEL.BBT202.XV01.CMD")
    c.arrow(868, 200)
    c.label(446, 300, "°F and °P in a fermenter; level % and CO2 volumes in a bright tank", size=9)
    status(c, 446, 322, "Transfer running", "CEL.XFER.YS01.PV")
    status(c, 590, 322, "Carbonation inhibited", "CEL.SYS.XS01.CO2_INHIBIT")
    c.link(430, 46, 420, 286, VIEW_ID["Cellar"], "Cellar")

    # -- packaging
    c.card(870, 46, 350, 286, "Packaging")
    c.word(884, 92, "PKG.LINE.PACKML.STATE", PACKML, size=20)
    c.label(884, 110, "bottling line", size=10)
    c.flags(884, 118, "PKG.LINE.PACKML.STATE")
    c.dial(1090, 54, 110, 96, "PKG.LINE.KQ01.OEE", 0, 100)
    c.label(1145, 156, "shift OEE %", size=10, anchor="middle")
    for i, m in enumerate(("RINSER", "FILLER", "CODER", "LABELER", "PACKER")):
        x = 884 + i * 66
        if i:
            c.path(f"M{x - 10:g} 191 L{x:g} 191", width=2)
        c.rect(x, 172, 56, 38, radius=3)
        c.label(x + 28, 187, m.capitalize(), size=9, color=INK, anchor="middle")
        c.step_lamp(x + 28, 199, f"PKG.{m}.PACKML.STATE", 6, r=4)
        c.flags(x + 21, 214, f"PKG.{m}.YA01.PV")
    row(c, 884, 256, "Filler speed", "PKG.FILLER.PACKML.SPEED", 0, "bottles/min", 200)
    row(c, 884, 278, "Bottles this shift", "PKG.LINE.PACKML.PROCESSED", 0, "", 200)
    row(c, 884, 300, "Rejected", "PKG.LINE.PACKML.DEFECTIVE", 0, "", 200)
    status(c, 884, 322, "Filler bowl low (no beer)", "PKG.FILLER.LSL01.PV")
    c.link(870, 46, 350, 286, VIEW_ID["Packaging"], "Packaging")

    # -- utilities
    c.card(20, 344, 600, 216, "Utilities")
    for i, (cap, tag, lo, hi, normal, unit, digits) in enumerate((
        ("Glycol", "UTL.CHL.TT01.PV", 20, 50, (26, 32), "°F", 1),
        ("Glycol", "UTL.GLY.PT01.PV", 0, 60, (25, 45), "psig", 0),
        ("Steam", "UTL.STM.PT01.PV", 0, 30, (10, 16), "psig", 1),
        ("Air", "UTL.AIR.PT01.PV", 0, 150, (90, 125), "psig", 0),
        ("CO2", "UTL.CO2.PT02.PV", 0, 100, (50, 70), "psig", 0),
        ("CO2 tank", "UTL.CO2.LT01.PV", 0, 100, (25, 100), "%", 0),
        ("Waste pH", "UTL.WWN.AT01.PV", 0, 14, (6, 9), "", 1),
    )):
        c.gauge(52 + i * 82, 392, 12, 118, tag, lo, hi, normal=normal, digits=digits, unit=unit, caption=cap)
    c.link(20, 344, 600, 216, VIEW_ID["Utilities"], "Utilities")

    # -- power
    c.card(630, 344, 590, 216, "Power")
    c.label(646, 384, "Utility", size=11, color=INK, bold=True)
    c.wire([(700, 380), (764, 380)])
    c.wire([(788, 380), (1196, 380)], "PWR.MAIN.ZS01.PV", width=6)
    c.switch(776, 380, "PWR.MAIN.ZS01.PV")
    c.label(990, 370, "480 V switchboard", size=10, anchor="middle")
    c.number(742, 404, "PWR.MAIN.JT01.PV", digits=0, size=16)
    c.label(744, 404, "kW import", size=9)
    for i, (cap, tag, sw) in enumerate((
        ("PV", "PWR.PV.JT01.PV", "PWR.PV.ZS01.PV"),
        ("Battery", "PWR.BESS.JT01.PV", "PWR.BESS.ZS01.PV"),
        ("Generator", "PWR.GEN.JT01.PV", "PWR.GEN.YS01.PV"),
    )):
        x = 850 + i * 130
        c.wire([(x, 380), (x, 440)], sw, width=3)
        c.switch(x, 412, sw, r=6)
        c.rect(x - 44, 440, 88, 56, radius=4)
        c.label(x, 456, cap, size=11, color=INK, anchor="middle", bold=True)
        c.number(x + 12, 480, tag, digits=0, size=15)
        c.label(x + 14, 480, "kW", size=9)
    c.hbar(646, 520, 150, 10, "PWR.MAIN.JQ01.PV", 0, 400, normal=(0, 250))
    c.label(646, 546, "15-minute demand", size=10)
    c.number(796, 546, "PWR.MAIN.JQ01.PV", digits=0, size=12)
    c.hbar(850, 520, 110, 10, "PWR.BESS.EY01.PV", 0, 100, normal=(50, 100), color=LIVE)
    c.label(850, 546, "battery charge", size=10)
    c.number(960, 546, "PWR.BESS.EY01.PV", digits=0, size=12)
    status(c, 1010, 524, "On-peak", "PWR.DMD.XS01.ONPEAK")
    status(c, 1010, 546, "Load shed", "PWR.DMD.XS02.SHED")
    status(c, 1100, 524, "On generator", "PWR.GEN.ZS01.PV")
    c.link(630, 344, 590, 216, VIEW_ID["Power"], "Power")

    # -- life safety and trends
    c.card(20, 572, 400, 232, "Cellar CO2 (life safety)")
    for i in range(4):
        c.gauge(50 + i * 62, 620, 12, 126, f"FAC.VENT.AT0{i + 1}.PV", 0, 35000, normal=(0, 5000), digits=0,
                unit="", caption=f"Sensor {i + 1}")
    c.label(140, 782, "ppm; marks at 5,000, 15,000, and 30,000", size=9, anchor="middle")
    c.fan(318, 630, "FAC.VENT.FAN01.PV")
    c.fan(356, 630, "FAC.VENT.FAN02.PV")
    c.label(337, 660, "purge fans", size=10, anchor="middle")
    status(c, 296, 688, "Tier 1", "FAC.VENT.AY01.TIER1")
    status(c, 296, 710, "Tier 2", "FAC.VENT.AY01.TIER2")
    status(c, 296, 732, "Tier 3", "FAC.VENT.AY01.TIER3")
    status(c, 296, 764, "CO2 supply on", "UTL.CO2.ZSO01.PV")
    c.link(20, 572, 400, 232, VIEW_ID["Cellar CO2"], "Cellar CO2")
    c.trend(430, 572, 390, 232, "Fermenters °F, last hour", [
        (f"CEL.{fv}.TT01.PV", fv[2:], color) for fv, color in zip(FVS, FV_COLORS, strict=True)
    ])
    c.trend(830, 572, 390, 232, "Power kW, last hour", [
        ("PWR.MAIN.JT01.PV", "Import", INK),
        ("PWR.PV.JT01.PV", "PV", MEDIA["beer"]),
        ("PWR.BESS.JT01.PV", "Battery", LIVE),
    ])
    return c.screen(VIEW_ID["Overview"])


# ---------------------------------------------------------------- brewhouse


def brewhouse(plant: Plant) -> Screen:
    """The brewhouse as its vessels stand: grain and liquor on the left, the mash
    tun and the kettle in the middle, the knockout to the cellar on the right,
    steam along the bottom, and the batch's phases across the top."""
    c: Canvas = canvas("Brewhouse", plant)
    c.title(20, 30, "Brewhouse")

    # -- the batch: ten phases as a train, the current one lit
    c.card(150, 10, 1070, 62)
    c.word(164, 34, "BH.BATCH.XC01.PHASE", PHASES, size=15)
    c.word(164, 56, "BH.BATCH.XS01.STATE", PHASE_STATE, size=12, bold=False)
    for n in range(1, 11):
        x: float = 320 + (n - 1) * 86
        if n < 10:
            c.path(f"M{x + 9:g} 32 L{x + 77:g} 32", stroke=RULE, width=2)
        c.step_lamp(x, 32, "BH.BATCH.XC01.PHASE", n, r=7)
        c.label(x, 58, PHASES[n], size=9, anchor="middle")
    c.label(1206, 34, "batch", size=9, anchor="end")
    c.number(1206, 56, "BH.BATCH.XI01.BATCH", digits=0, size=13)
    c.link(150, 10, 1070, 62, VIEW_ID["Batch"], "Batch")

    # -- pipes first, so the vessels stand on them
    c.pipe_run([(88, 318), (499, 318), (499, 286)], "grain", 4, flow="BH.MILL.YS01.PV")
    c.pipe_run([(126, 160), (162, 160)], "water", 4, flow="BH.CLT.XV01.CMD")
    c.pipe_run([(256, 196), (290, 196)], "water", 4, flow="BH.HLT.XV01.CMD")
    c.pipe_run([(386, 180), (424, 180)], "hot", 4, flow="BH.MLT.XV01.CMD")
    c.pipe_run([(386, 214), (424, 214)], "hot", 4, flow="BH.MLT.FT02.PV", above=1)
    c.pipe_run([(574, 250), (650, 250)], "wort", 5, flow="BH.MLT.FT01.PV", above=1)
    c.pipe_run([(800, 250), (1212, 250)], "wort", 5, flow="BH.KO.FT01.PV", above=1)
    c.pipe_run([(208, 270), (208, 352), (960, 352), (960, 278)], "water", 3)
    c.pipe_run([(1010, 278), (1010, 376), (20, 376)], "glycol", 3)
    c.pipe_run([(20, 428), (760, 428)], "steam", 5)

    # -- grain
    c.rect(30, 130, 90, 110, radius=3)
    c.path("M30 240 L75 290 L120 240 Z", fill=BODY)
    c.label(75, 122, "Grain and mill", size=12, color=INK, anchor="middle", bold=True)
    c.level(42, 146, 16, 84, "BH.MILL.LT01.PV", 0, 100, "grain")
    c.number(104, 184, "BH.MILL.LT01.PV", digits=0, size=16)
    c.label(106, 184, "%", size=9)
    c.pump(75, 318, "BH.MILL.YS01.PV", r=13)
    c.label(75, 348, "mill", size=10, anchor="middle")
    c.number(128, 336, "BH.MILL.SI01.PV", digits=0, size=11, bold=False)
    c.label(130, 336, "Hz", size=9)

    # -- liquor
    for i, (unit, name, medium) in enumerate((("CLT", "Cold liquor", "water"), ("HLT", "Hot liquor", "hot"))):
        x = 160 + i * 130
        vessel(c, x, 120, 96, 150, f"BH.{unit}.LT01.PV", medium)
        c.label(x + 48, 112, name, size=12, color=INK, anchor="middle", bold=True)
        c.number(x + 66, 176, f"BH.{unit}.TT01.PV", digits=1, size=18)
        c.label(x + 68, 176, "°F", size=10, color=INK)
        c.number(x + 66, 200, f"BH.{unit}.LT01.PV", digits=0, size=12, bold=False)
        c.label(x + 68, 200, "%", size=10, color=INK)
        c.flags(x + 30, 212, f"BH.{unit}.TT01.PV")
        c.flags(x + 30, 230, f"BH.{unit}.LT01.PV")
    c.label(304, 300, "set °F", size=9)
    c.entry(336, 285, "BH.HLT.TT01.SP", 60, 200, digits=0)
    c.valve(143, 160, "BH.CLT.XV01.CMD", size=6)
    c.label(140, 176, "RO in", size=9, anchor="middle")
    c.valve(273, 196, "BH.HLT.XV01.CMD", size=7)
    c.valve(405, 180, "BH.MLT.XV01.CMD", size=6)
    c.label(405, 170, "strike", size=9, anchor="middle")
    c.valve(405, 214, "BH.MLT.FV02.OUT", size=6, above=1)
    c.label(405, 232, "sparge", size=9, anchor="middle")

    # -- mash/lauter tun
    vessel(c, 424, 100, 150, 190, "BH.MLT.LT01.PV", "wort")
    c.label(499, 92, "Mash/lauter tun", size=12, color=INK, anchor="middle", bold=True)
    c.number(540, 156, "BH.MLT.TT01.PV", digits=1, size=24)
    c.label(542, 156, "°F", size=11, color=INK)
    c.label(448, 182, "set", size=10, color=INK)
    c.number(540, 182, "BH.MLT.TT01.SP", digits=0, size=12, bold=False)
    c.label(448, 204, "bed dP", size=10, color=INK)
    c.number(540, 204, "BH.MLT.PDT01.PV", digits=2, size=12)
    c.label(542, 204, "psi", size=9, color=INK)
    c.label(448, 226, "rake", size=10, color=INK)
    c.fan(530, 222, "BH.MLT.YS01.PV", r=9)
    c.flags(448, 238, "BH.MLT.TT01.PV")
    c.flags(482, 238, "BH.MLT.PDT01.PV")
    c.flags(516, 238, "BH.MLT.LT01.PV")
    c.valve(612, 250, "BH.MLT.FV01.OUT", above=1)
    c.number(622, 236, "BH.MLT.FT01.PV", digits=0, size=11, bold=False)
    c.label(612, 274, "runoff gal/min", size=9, anchor="middle")

    # -- kettle
    vessel(c, 650, 100, 150, 190, "BH.KTL.LT01.PV", "wort")
    c.label(725, 92, "Kettle and whirlpool", size=12, color=INK, anchor="middle", bold=True)
    c.number(766, 156, "BH.KTL.TT01.PV", digits=1, size=24)
    c.label(768, 156, "°F", size=11, color=INK)
    c.label(674, 182, "boils at", size=10, color=INK)
    c.number(766, 182, "BH.KTL.TB01.PV", digits=1, size=12, bold=False)
    c.label(674, 204, "boil time", size=10, color=INK)
    c.number(766, 204, "BH.KTL.KI01.PV", digits=0, size=12)
    c.label(768, 204, "min", size=9, color=INK)
    status(c, 674, 228, "Boiling", "BH.KTL.XS01.BOIL")
    status(c, 664, 308, "Whirlpool inlet", "BH.KTL.XV01.CMD")
    status(c, 664, 328, "Hop doser", "BH.KTL.XV02.CMD")
    c.flags(674, 238, "BH.KTL.LT01.PV")
    c.flags(708, 238, "BH.KTL.TT01.PV")

    # -- knockout: pump, heat exchanger, oxygen, to the cellar
    c.pump(876, 250, "BH.KO.YS01.PV", r=13)
    c.rect(930, 222, 110, 56, radius=3)
    c.label(985, 214, "Wort cooler", size=11, color=INK, anchor="middle", bold=True)
    for k in range(5):
        c.path(f"M{944 + k * 20:g} 228 L{944 + k * 20:g} 272", stroke=DIM, width=1)
    c.number(880, 300, "BH.KO.TT01.PV", digits=0, size=12)
    c.label(882, 300, "°F after stage 1", size=9)
    c.number(1076, 300, "BH.KO.TT02.PV", digits=1, size=15)
    c.label(1078, 300, "°F out", size=9)
    c.label(1040, 322, "set", size=9)
    c.entry(1058, 308, "BH.KO.TT02.SP", 40, 90, w=46, digits=0)
    c.flags(1112, 290, "BH.KO.TT02.PV")
    c.valve(1150, 250, "BH.KO.XV01.CMD")
    c.arrow(1222, 250)
    c.label(1212, 236, "to a fermenter", size=10, anchor="end")
    c.number(1166, 286, "BH.KO.FT01.PV", digits=0, size=14)
    c.label(1168, 286, "gal/min", size=9)
    c.number(1166, 308, "BH.KO.AT01.PV", digits=1, size=12)
    c.label(1168, 308, "ppm O2", size=9)
    c.flags(1206, 316, "BH.KO.AT01.PV")
    c.label(440, 346, "cold liquor to the cooler's first stage", size=9, anchor="middle")
    c.label(440, 370, "glycol through the cooler's second stage", size=9, anchor="middle")

    # -- steam along the bottom, a branch to each jacket
    c.label(24, 418, "steam", size=10)
    row(c, 60, 418, "", "UTL.STM.PT01.PV", 1, "psig", 70)
    for x, tag, top in ((338, "BH.HLT.TV01.OUT", 270), (540, "BH.MLT.TV01.OUT", 290), (760, "BH.KTL.TV01.OUT", 290)):
        c.pipe_run([(x, 428), (x, top)], "steam", 3, flow=tag, above=1)
        c.valve(x, 400, tag, size=6, vertical=True, above=1)
        c.hbar(x + 14, 396, 50, 8, tag, 0, 100, color=MEDIA["hot"])
        c.number(x + 92, 404, tag, digits=0, size=11)
        c.label(x + 94, 404, "%", size=9)

    # -- trends and the rest of the numbers
    c.trend(20, 450, 600, 354, "Temperatures °F, last hour", [
        ("BH.MLT.TT01.PV", "Mash tun", MEDIA["wort"]),
        ("BH.KTL.TT01.PV", "Kettle", INK),
        ("BH.HLT.TT01.PV", "Hot liquor", MEDIA["hot"]),
        ("BH.KO.TT02.PV", "Wort to cellar", LIVE),
    ])
    c.card(630, 450, 290, 354, "Flows and states")
    row(c, 646, 490, "Sparge water", "BH.MLT.FT02.PV", 0, "gal/min", 220)
    row(c, 646, 514, "Runoff to the kettle", "BH.MLT.FT01.PV", 0, "gal/min", 220)
    row(c, 646, 538, "Knockout wort", "BH.KO.FT01.PV", 0, "gal/min", 220)
    row(c, 646, 562, "Oxygen", "BH.KO.FT02.PV", 1, "L/min", 220)
    row(c, 646, 586, "Rake height", "BH.MLT.ZT01.PV", 1, "in", 220)
    row(c, 646, 610, "Barometric pressure", "BH.KTL.PT01.PV", 1, "psia", 220)
    status(c, 646, 648, "Mash step holding", "BH.MLT.XS01.ACTIVE")
    status(c, 646, 672, "Cold liquor high level", "BH.CLT.LSH01.PV")
    status(c, 646, 696, "Hot liquor low level", "BH.HLT.LSL01.PV")
    status(c, 646, 720, "Kettle high level", "BH.KTL.LSH01.PV")
    status(c, 646, 752, "Mash tun locked for entry", "BH.ENTRY.XS01.LOCKED")
    c.flags(820, 741, "BH.ENTRY.XA01.PV")
    status(c, 646, 776, "Kettle locked for entry", "BH.ENTRY.XS02.LOCKED")
    c.flags(820, 765, "BH.ENTRY.XA02.PV")
    c.card(930, 450, 290, 354, "Batch")
    row(c, 946, 490, "Brews requested at the panel", "BH.BATCH.XI02.REQUESTS", 0, "", 240)
    row(c, 946, 514, "Requests the engine accepted", "BH.BATCH.XC02.ACCEPTED", 0, "", 240)
    row(c, 946, 538, "Step in the phase", "BH.BATCH.XS01.STEP", 0, "", 240)
    c.label(946, 580, "The batch engine runs the phases above.", size=11)
    c.label(946, 598, "Start, hold, and abort a brew from the", size=11)
    c.label(946, 616, "Batch view: click here or the phase bar.", size=11)
    c.link(930, 450, 290, 354, VIEW_ID["Batch"], "Batch")
    return c.screen(VIEW_ID["Brewhouse"])


# ---------------------------------------------------------------- cellar


def cellar(plant: Plant) -> Screen:
    """The cellar: the four fermenters and, where the plant has them, the two
    bright tanks, with beer moving left to right toward the filler."""
    c: Canvas = canvas("Cellar", plant)
    whole: bool = c.has("CEL.BBT201.LT01.PV")
    c.title(20, 30, "Cellar")
    c.label(100, 30, "fermentation and conditioning", size=12)
    row(c, 1010, 30, "Fermentation clock", "CEL.CLK.KI01.PV", 1, "h", 190)
    if whole:
        row(c, 790, 30, "Glycol supply", "UTL.CHL.TT01.PV", 1, "°F", 160)

    c.label(20, 62, "wort from the brewhouse", size=10)
    knockout: str | None = "BH.KO.FT01.PV" if whole else None
    c.pipe_run([(20, 74), (34 + 3 * 182 + 50, 74)], "wort", flow=knockout, above=1)
    if whole:
        # Racking: a stub from each cone to the header, the transfer pump, BBT-201.
        for i in range(4):
            cx: float = 34 + i * 182 + 50
            c.pipe_run([(cx, 352), (cx, 388)], "beer", 3)
        c.pipe_run([(84, 388), (784, 388), (784, 300), (812, 300)], "beer", flow="CEL.XFER.YS01.PV")
        c.pipe_run([(932, 330), (1008, 330)], "beer")
        c.pipe_run([(1128, 330), (1214, 330)], "beer", flow="CEL.BBT202.XV01.CMD")
    for i, fv in enumerate(FVS):
        _fermenter(c, 34 + i * 182, 100, fv, i + 1, whole)

    if whole:
        c.pump(760, 388, "CEL.XFER.YS01.PV")
        c.label(760, 416, "transfer pump", size=10, anchor="middle")
        c.number(764, 432, "CEL.XFER.FT01.PV", digits=0, size=11, bold=False)
        c.label(766, 432, "gal/min", size=9)
        for j, bbt in enumerate(BBTS):
            _bright_tank(c, 812 + j * 196, 110, bbt)
        c.valve(1166, 330, "CEL.BBT202.XV01.CMD")
        c.arrow(1224, 330)
        c.label(1214, 316, "to the filler", size=10, anchor="end")

    c.trend(20, 520, 500, 284, "Fermenters °F, last hour", [
        (f"CEL.{fv}.TT01.PV", fv[2:], color) for fv, color in zip(FVS, FV_COLORS, strict=True)
    ])
    c.trend(530, 520, 350, 284, "Extract °P, last hour", [
        (f"CEL.{fv}.DT01.PV", fv[2:], color) for fv, color in zip(FVS, FV_COLORS, strict=True)
    ])
    if whole:
        c.card(890, 520, 330, 284, "Safety and transfer")
        status(c, 906, 558, "CO2 relay healthy", "CEL.SYS.XS01.CO2_OK")
        status(c, 906, 582, "Carbonation inhibited", "CEL.SYS.XS01.CO2_INHIBIT")
        status(c, 906, 606, "Transfer route verified", "CEL.XFER.ZS01.PV")
        status(c, 906, 630, "CIP permitted", "CEL.XFER.XS02.CIP_PERMIT")
        c.label(906, 672, "Relief valve lifted", size=10)
        c.label(906, 722, "Locked for entry", size=10)
        for k, name in enumerate((*FVS, *BBTS)):
            x: float = 918 + k * 50
            c.label(x, 778, name.replace("FV", "").replace("BBT", ""), size=9, anchor="middle")
            c.state_circle(x, 690, 6, f"CEL.PSV.ZSH0{k + 1}.PV")
            c.flags(x + 9, 678, f"CEL.PSV.ZSH0{k + 1}.PV")
            c.state_circle(x, 740, 6, f"CEL.ENTRY.XS0{k + 1}.LOCKED")
            c.flags(x + 9, 728, f"CEL.ENTRY.XA0{k + 1}.PV")
    else:
        _legend(c, 790, 90)
        c.card(890, 520, 330, 284, "This plant")
        c.label(906, 558, "Four fermenters on plc-cellar.", size=11)
        c.label(906, 578, "Set a side or cone temperature in the", size=11)
        c.label(906, 596, "box under its fermenter.", size=11)
    return c.screen(VIEW_ID["Cellar"])


def _legend(c: Canvas, x: float, y: float) -> None:
    """How to read the Cellar, drawn where the ci plant has no bright tanks."""
    c.card(x, y, 430, 330, "Reading this screen")
    for k, (color, text) in enumerate(((MEDIA["wort"], "wort, 9 °P or more"), ("#C99F45", "fermenting"),
                                       (MEDIA["beer"], "beer, under 4 °P"), (BODY, "empty"))):
        c.rect(x + 16 + k * 104, y + 34, 18, 18, fill=color, width=1)
        c.label(x + 40 + k * 104, y + 47, text, size=10, color=INK)
    c.label(x + 16, y + 74, "A fermenter's color is what it holds, from its extract.", size=10)
    c.rect(x + 16, y + 92, 6, 30, fill=MEDIA["glycol"], width=1)
    c.rect(x + 28, y + 92, 6, 30, fill=WHITE, width=1)
    c.label(x + 44, y + 104, "A jacket band is filled while its glycol valve is open,", size=10, color=INK)
    c.label(x + 44, y + 118, "and empty while it is closed.", size=10, color=INK)
    # a scale, labeled
    sx, sy = x + 30, y + 150
    c.rect(sx, sy, 10, 110, fill=WHITE, stroke=DIM, width=1)
    c.rect(sx, sy + 30, 10, 50, fill=BAND, stroke="none", width=0)
    c.rect(sx, sy, 10, 110, fill="none", stroke=DIM, width=1)
    c.path(f"M{sx - 4:g} {sy + 14:g} L{sx + 10:g} {sy + 14:g}", width=1.5)
    c.path(f"M{sx - 2:g} {sy + 52:g} L{sx + 12:g} {sy + 52:g} l7 -5 l0 10 l-7 -5 Z", fill=INK)
    c.path(f"M{sx - 8:g} {sy + 60:g} l6 4 l-6 4 Z", fill=WHITE, width=1)
    for ly, text in ((sy + 18, "a line is an alarm limit"), (sy + 56, "the pointer is the value"),
                     (sy + 76, "the hollow marker on the left is the setpoint"),
                     (sy + 96, "the shaded band is the normal range")):
        c.label(sx + 34, ly, text, size=10, color=INK)
    c.label(x + 16, y + 286, "A shape with a number beside a value is an active alarm:", size=10)
    c.label(x + 16, y + 302, "1 critical (square), 2 high, 3 medium, 4 low (diamond). Hold the", size=10)
    c.label(x + 16, y + 318, "pointer on it for its text. The bell in the header lists them all.", size=10)


def _fermenter(c: Canvas, x: float, y: float, fv: str, n: int, whole: bool) -> None:
    """One unitank at (x, y): the drop from the wort header, the glycol jackets,
    the numbers on the vessel, its temperature and head pressure on scales
    beside it, and its setpoints and the profile's stage under it."""
    w, h, cone = 100.0, 190.0, 62.0
    cx: float = x + w / 2
    t: str = f"CEL.{fv}"
    c.pipe_run([(cx, 74), (cx, y + 4)], "wort", width=4)
    c.unitank(x, y, w, h, cone)
    c.shape("path", {"d": c.unitank_outline(x, y, w, h, cone), "fill": BODY, "stroke": INK, "stroke-width": 1.5},
            f"{fv} contents", tag_name=f"{t}.DT01.PV",
            ranges=[{"min": a, "max": b, "color": color} for a, b, color in FERMENTING])
    # glycol jackets: a band on the side and one on the cone, filled while they cool
    for jx in (x - 7, x + w + 1):
        c.shape("rect", {"x": f"{jx:g}", "y": f"{y + 40:g}", "width": 6, "height": 110, "fill": WHITE,
                         "stroke": INK, "stroke-width": 1}, f"{t}.TV01.CMD jacket", tag_name=f"{t}.TV01.CMD",
                ranges=[{"min": 0, "max": 0, "color": WHITE}, {"min": 1, "max": 1, "color": MEDIA["glycol"]}])
    for d in (f"M{x - 7:g} {y + h + 2:g} L{x - 1:g} {y + h + 2:g} L{cx - 13:g} {y + h + cone - 8:g} L{cx - 19:g} {y + h + cone - 8:g} Z",
              f"M{x + w + 7:g} {y + h + 2:g} L{x + w + 1:g} {y + h + 2:g} L{cx + 13:g} {y + h + cone - 8:g} L{cx + 19:g} {y + h + cone - 8:g} Z"):
        c.shape("path", {"d": d, "fill": WHITE, "stroke": INK, "stroke-width": 1}, f"{t}.TV02.CMD jacket",
                tag_name=f"{t}.TV02.CMD",
                ranges=[{"min": 0, "max": 0, "color": WHITE}, {"min": 1, "max": 1, "color": MEDIA["glycol"]}])
    c.label(cx, y + 34, f"FV-{fv[2:]}", size=15, color=INK, anchor="middle", bold=True)
    c.number(cx + 22, y + 76, f"{t}.TT01.PV", digits=1, size=24)
    c.label(cx + 24, y + 76, "°F", size=11, color=INK)
    c.label(cx - 38, y + 104, "cone", size=10, color=INK)
    c.number(cx + 22, y + 104, f"{t}.TT02.PV", digits=1, size=12, bold=False)
    c.label(cx - 38, y + 130, "extract", size=10, color=INK)
    c.number(cx + 22, y + 130, f"{t}.DT01.PV", digits=1, size=13)
    c.label(cx + 24, y + 130, "°P", size=10, color=INK)
    c.flags(cx - 38, y + 146, f"{t}.TT01.PV")
    c.flags(cx, y + 146, f"{t}.TT02.PV")
    # scales: temperature with its setpoint, head pressure with its relief setting
    c.label(x + w + 22, y + 22, "°F", size=9, anchor="middle")
    c.scale(x + w + 17, y + 30, 10, 150, f"{t}.TT01.PV", 30.0, 80.0, setpoint=f"{t}.TT01.SP")
    c.label(x + w + 52, y + 22, "psia", size=9, anchor="middle")
    c.scale(x + w + 47, y + 30, 10, 150, f"{t}.PT01.PV", 12.0, 30.0, normal=(13.0, 26.1))
    c.number(x + w + 64, y + 196, f"{t}.PT01.PV", digits=1, size=11)
    c.flags(x + w + 40, y + 204, f"{t}.PT01.PV")
    c.state_circle(x + w + 52, y + 8, 4, f"{t}.ZSO01.PV", on=MEDIA["co2"])
    # setpoints
    base: float = y + h + cone + 54
    c.label(x - 6, base, "set  side", size=9)
    c.entry(x + 34, base - 15, f"{t}.TT01.SP", 20, 90, w=44, digits=0)
    c.label(x + 84, base, "cone", size=9)
    c.entry(x + 106, base - 15, f"{t}.TT02.SP", 20, 90, w=44, digits=0)
    if whole:
        c.label(x - 6, base + 26, "spund set", size=9)
        c.number(x + 66, base + 26, f"{t}.PCV01.SP", digits=1, size=11)
        c.label(x + 68, base + 26, "psia", size=9)
        c.word(x + 154, base + 26, f"CEL.PROFILE.HS0{n}.AUTO", {0: "by hand", 1: "profile on"}, size=10,
               anchor="end", bold=False)
        for k, text in enumerate(("ferment", "rest", "crash")):
            c.step_lamp(x + k * 54, base + 48, f"CEL.PROFILE.KS0{n}.STAGE", k + 1, r=5)
            c.label(x + 8 + k * 54, base + 52, text, size=9)
        c.button(x - 8, base + 60, 84, 26, "Profile on", f"CEL.PROFILE.HS0{n}.AUTO", 1)
        c.button(x + 78, base + 60, 76, 26, "By hand", f"CEL.PROFILE.HS0{n}.AUTO", 0)


def _bright_tank(c: Canvas, x: float, y: float, bbt: str) -> None:
    w, h = 120.0, 250.0
    cx: float = x + w / 2
    t: str = f"CEL.{bbt}"
    vessel(c, x, y, w, h, f"{t}.LT01.PV", "beer")
    c.shape("rect", {"x": f"{x + w + 1:g}", "y": f"{y + 50:g}", "width": 6, "height": 140, "fill": WHITE,
                     "stroke": INK, "stroke-width": 1}, f"{t}.TV01.CMD jacket", tag_name=f"{t}.TV01.CMD",
            ranges=[{"min": 0, "max": 0, "color": WHITE}, {"min": 1, "max": 1, "color": MEDIA["glycol"]}])
    c.label(cx, y + 30, f"BBT-{bbt[3:]}", size=15, color=INK, anchor="middle", bold=True)
    c.number(cx + 22, y + 74, f"{t}.LT01.PV", digits=0, size=24)
    c.label(cx + 24, y + 74, "%", size=11, color=INK)
    for k, (cap, tag, digits) in enumerate((("°F", "TT01.PV", 1), ("psia", "PT01.PV", 1), ("vol CO2", "AT01.PV", 2))):
        c.number(cx + 6, y + 104 + k * 24, f"{t}.{tag}", digits=digits, size=13)
        c.label(cx + 9, y + 104 + k * 24, cap, size=9, color=INK)
    c.flags(x + 22, y + 164, f"{t}.TT01.PV")
    c.flags(x + 58, y + 164, f"{t}.PT01.PV")
    c.flags(x + 94, y + 164, f"{t}.LT01.PV")
    c.label(x + 18, y + 200, "stone", size=9, color=INK)
    c.hbar(x + 50, y + 192, 40, 8, f"{t}.FCV01.OUT", 0, 100, color=MEDIA["co2"])
    c.number(x + w - 6, y + 200, f"{t}.FCV01.OUT", digits=0, size=10, bold=False)
    base: float = y + h + 30
    c.label(x - 4, base, "set °F", size=9)
    c.entry(x + 28, base - 15, f"{t}.TT01.SP", 28, 60, w=40, digits=0)
    c.label(x + 74, base, "vol", size=9)
    c.entry(x + 90, base - 15, f"{t}.AT01.SP", 0, 4, w=46, digits=2)


# ---------------------------------------------------------------- packaging


def packaging(plant: Plant) -> Screen:
    """The bottling line as it stands on the floor, glass coming in on the left
    and cases leaving on the right, with the keg line under it."""
    c: Canvas = canvas("Packaging", plant)
    c.title(20, 30, "Packaging")

    # -- the line as a whole, and its commands
    c.card(20, 46, 1200, 110, "Bottling line")
    c.word(36, 96, "PKG.LINE.PACKML.STATE", PACKML, size=26)
    c.flags(36, 110, "PKG.LINE.PACKML.STATE")
    c.dial(262, 50, 120, 100, "PKG.LINE.KQ01.OEE", 0, 100)
    c.label(322, 150, "shift OEE %", size=10, anchor="middle")
    row(c, 410, 78, "Bottles this shift", "PKG.LINE.PACKML.PROCESSED", 0, "", 200)
    row(c, 410, 102, "Rejected", "PKG.LINE.PACKML.DEFECTIVE", 0, "", 200)
    row(c, 410, 126, "Stop reason (first machine out)", "PKG.LINE.PACKML.STOP_REASON", 0, "", 200)
    c.label(650, 78, "Speed setpoint", size=11)
    c.entry(748, 63, "PKG.LINE.PACKML.SPEED_SP", 0, 60, digits=0)
    c.label(810, 78, "bottles/min", size=9)
    status(c, 650, 104, "Line not stopped or aborted", "PKG.LINE.XS01.RUN")
    status(c, 650, 128, "Panel button pressed", "PKG.PANEL.HS01.PV")
    for k, (text, value) in enumerate((("Reset", 1), ("Start", 2), ("Stop", 3), ("Hold", 4), ("Unhold", 5), ("Clear", 9))):
        c.button(880 + (k % 3) * 110, 62 + (k // 3) * 42, 100, 32, text, "PKG.LINE.PACKML.CMD", value)

    # -- beer in from BBT-202
    c.pipe_run([(20, 200), (504, 200), (504, 250)], "beer", 5, flow="PKG.FILLER.XV01.CMD")
    c.label(24, 190, "beer from BBT-202", size=10)
    c.valve(440, 200, "PKG.FILLER.XV01.CMD")
    row(c, 150, 190, "", "PKG.FILLER.PT01.PV", 1, "psia in the bowl", 90)
    status(c, 300, 226, "Bowl low: no beer", "PKG.FILLER.LSL01.PV")

    # -- the machines, joined by conveyors that move while they run
    machines: tuple[tuple[str, str], ...] = (("RINSER", "Rinser"), ("FILLER", "Filler/crowner"),
                                             ("CODER", "Date coder"), ("LABELER", "Labeler"),
                                             ("PACKER", "Case packer"))
    # conveyor 1 feeds the rinser (a block with the filler), 2 runs from the filler
    # to the labeler, 3 from the labeler to the packer, 4 takes the cases away
    belts: tuple[tuple[float, float, int], ...] = ((20, 70, 1), (242, 302, 1), (474, 534, 2), (706, 766, 2),
                                                   (938, 998, 3), (1170, 1214, 4))
    for x0, x1, n in belts:
        c.pipe_run([(x0, 330), (x1, 330)], "air", 10, flow=f"PKG.CONV.HS0{n}.CMD")
    for n, bx in ((1, 272), (2, 504), (3, 968), (4, 1196)):
        c.label(bx, 356, f"conveyor {n}", size=9, anchor="middle")
        c.state_circle(bx - 22, 372, 4, f"PKG.CONV.LS0{n}.PV")
        c.label(bx - 15, 375, "backed up", size=8)
        c.flags(bx - 7, 382, f"PKG.CONV.YA0{n}.PV")
    for i, (unit, name) in enumerate(machines):
        _machine(c, 70 + i * 232, 250, unit, name)
    c.label(20, 312, "glass in", size=10)
    c.arrow(1232, 330)
    c.label(1226, 312, "cases", size=10, anchor="end")

    # -- kegs, and the line's last hour
    c.card(20, 470, 380, 334, "Keg washer and filler")
    c.word(36, 512, "PKG.KEG.PACKML.STATE", PACKML, size=20)
    row(c, 36, 546, "Kegs filled", "PKG.KEG.PACKML.PROCESSED", 0, "", 220)
    row(c, 36, 570, "Kegs rejected", "PKG.KEG.PACKML.DEFECTIVE", 0, "", 220)
    row(c, 36, 594, "Stop reason", "PKG.KEG.PACKML.STOP_REASON", 0, "", 220)
    c.gauge(60, 650, 12, 110, "PKG.KEG.TT01.PV", 60, 200, normal=(165, 175), digits=0, unit="°F",
            caption="Wash water")
    status(c, 150, 680, "Wash heater on", "PKG.KEG.HS01.CMD")
    status(c, 150, 706, "Jammed", "PKG.KEG.YA01.PV")
    c.trend(410, 470, 810, 334, "Line, last hour", [
        ("PKG.FILLER.PACKML.SPEED", "Filler bottles/min", INK),
        ("PKG.PACKER.PACKML.SPEED", "Packer bottles/min", LIVE),
        ("PKG.FILLER.PT01.PV", "Bowl psia", MEDIA["beer"]),
    ])
    return c.screen(VIEW_ID["Packaging"])


def _machine(c: Canvas, x: float, y: float, unit: str, name: str) -> None:
    t: str = f"PKG.{unit}"
    c.rect(x, y, 172, 190, radius=5)
    # the header band is filled while the machine is in Execute
    c.shape("rect", {"x": f"{x:g}", "y": f"{y:g}", "width": 172, "height": 28, "rx": 5, "fill": WHITE,
                     "stroke": INK, "stroke-width": 1.5}, f"{t} running", tag_name=f"{t}.PACKML.STATE",
            ranges=[{"min": -1, "max": 5, "color": WHITE}, {"min": 6, "max": 6, "color": "#A9C8D8"},
                    {"min": 7, "max": 99, "color": WHITE}])
    c.label(x + 86, y + 19, name, size=13, color=INK, anchor="middle", bold=True)
    c.word(x + 86, y + 50, f"{t}.PACKML.STATE", PACKML, size=16, anchor="middle")
    c.number(x + 96, y + 82, f"{t}.PACKML.SPEED", digits=0, size=20)
    c.label(x + 98, y + 82, "bottles/min", size=9, color=INK)
    c.hbar(x + 14, y + 92, 144, 8, f"{t}.PACKML.SPEED", 0, 60, color=LIVE)
    c.label(x + 14, y + 124, "made", size=10, color=INK)
    c.number(x + 158, y + 124, f"{t}.PACKML.PROCESSED", digits=0, size=12)
    c.label(x + 14, y + 144, "rejected", size=10, color=INK)
    c.number(x + 158, y + 144, f"{t}.PACKML.DEFECTIVE", digits=0, size=12)
    c.label(x + 14, y + 164, "stop reason", size=10, color=INK)
    c.number(x + 158, y + 164, f"{t}.PACKML.STOP_REASON", digits=0, size=12)
    c.state_circle(x + 20, y + 178, 4, f"{t}.YA01.PV")
    c.label(x + 30, y + 182, "jammed", size=10, color=INK)
    c.flags(x + 76, y + 171, f"{t}.YA01.PV")


# ---------------------------------------------------------------- utilities


def utilities(plant: Plant) -> Screen:
    """The services: glycol, steam, air, CO2, and water across the top, the
    wastewater pit and the CIP skid under them."""
    c: Canvas = canvas("Utilities", plant)
    c.title(20, 30, "Utilities")

    c.card(20, 46, 380, 330, "Glycol")
    c.pipe_run([(138, 124), (176, 124)], "glycol", 5, flow="UTL.CHL.YS01.PV")
    c.pipe_run([(214, 196), (214, 222), (392, 222)], "glycol", 5, flow="UTL.GLY.YS01.PV")
    c.rect(36, 84, 102, 84, radius=4)
    c.label(87, 102, "Chiller", size=12, color=INK, anchor="middle", bold=True)
    c.state_circle(66, 132, 9, "UTL.CHL.YS01.PV")
    c.state_circle(108, 132, 9, "UTL.CHL.YS02.PV")
    c.label(87, 158, "stage 1      stage 2", size=9, anchor="middle")
    c.flags(36, 174, "UTL.CHL.YA01.PV")
    vessel(c, 176, 76, 76, 124, "UTL.GLY.LT01.PV", "glycol")
    c.number(226, 146, "UTL.GLY.LT01.PV", digits=0, size=16)
    c.label(228, 146, "%", size=10, color=INK)
    c.flags(258, 84, "UTL.GLY.LT01.PV", down=True)
    c.pump(300, 222, "UTL.GLY.YS01.PV")
    c.arrow(396, 222)
    c.label(384, 208, "to the jackets", size=9, anchor="end")
    row(c, 280, 100, "Chiller", "UTL.CHL.JT01.PV", 0, "kW", 100)
    row(c, 280, 124, "Refrigerant", "UTL.CHL.AT01.PV", 0, "ppm", 100)
    row(c, 280, 148, "Pump", "UTL.GLY.SC01.OUT", 0, "%", 100)
    c.gauge(56, 264, 12, 80, "UTL.CHL.TT01.PV", 20, 50, normal=(26, 32), digits=1, unit="°F", caption="Supply",
            setpoint="UTL.CHL.TT01.SP")
    c.gauge(146, 264, 12, 80, "UTL.CHL.TT02.PV", 20, 60, normal=(28, 40), digits=1, unit="°F", caption="Return")
    c.gauge(236, 264, 12, 80, "UTL.GLY.PT01.PV", 0, 60, normal=(25, 45), digits=0, unit="psig", caption="Header",
            setpoint="UTL.GLY.PT01.SP")
    c.label(296, 282, "supply set °F", size=9)
    c.entry(296, 288, "UTL.CHL.TT01.SP", 20, 50)
    c.label(296, 330, "header set psig", size=9)
    c.entry(296, 336, "UTL.GLY.PT01.SP", 0, 100, digits=0)

    c.card(410, 46, 200, 330, "Steam")
    c.tank(440, 84, 110, 120)
    c.label(495, 120, "Boiler", size=12, color=INK, anchor="middle", bold=True)
    c.state_circle(495, 148, 10, "UTL.STM.YS01.PV", on=MEDIA["hot"])
    c.label(495, 178, "firing", size=9, anchor="middle")
    c.gauge(446, 264, 12, 80, "UTL.STM.PT01.PV", 0, 30, normal=(10, 16), digits=1, unit="psig", caption="Pressure",
            setpoint="UTL.STM.PC01.SP")
    row(c, 496, 276, "Power", "UTL.STM.JT01.PV", 0, "kW", 100)
    row(c, 496, 302, "Setpoint", "UTL.STM.PC01.SP", 0, "psig", 100)
    status(c, 500, 336, "Low water", "UTL.STM.LSL01.PV")

    c.card(620, 46, 150, 330, "Compressed air")
    c.pump(695, 120, "UTL.AIR.YS01.PV", r=18)
    c.label(695, 158, "compressor", size=10, anchor="middle")
    c.flags(688, 166, "UTL.AIR.YA01.PV")
    c.gauge(676, 264, 12, 80, "UTL.AIR.PT01.PV", 0, 150, normal=(90, 125), digits=0, unit="psig", caption="Header")

    c.card(780, 46, 250, 330, "CO2 supply")
    c.pipe_run([(866, 124), (1022, 124)], "co2", 5, flow="UTL.CO2.ZSO01.PV")
    vessel(c, 796, 84, 70, 140, "UTL.CO2.LT01.PV", "co2")
    c.number(846, 160, "UTL.CO2.LT01.PV", digits=0, size=15)
    c.label(848, 160, "%", size=10, color=INK)
    c.flags(824, 174, "UTL.CO2.LT01.PV")
    c.valve(926, 124, "UTL.CO2.ZSO01.PV")
    c.label(926, 148, "shutoff", size=9, anchor="middle")
    c.arrow(1026, 124)
    c.gauge(806, 264, 12, 80, "UTL.CO2.PT01.PV", 0, 350, normal=(200, 320), digits=0, unit="psig", caption="Tank")
    c.gauge(886, 264, 12, 80, "UTL.CO2.PT02.PV", 0, 100, normal=(50, 70), digits=0, unit="psig", caption="Header")
    status(c, 890, 184, "Shutoff relay healthy", "UTL.CO2.XS01.CO2_OK")
    status(c, 890, 208, "Supply shut off (latched)", "UTL.CO2.XS02.SHUTOFF")
    c.button(946, 300, 70, 28, "Reset", "UTL.CO2.XC01.RESET", 1)

    c.card(1040, 46, 180, 330, "Water")
    c.box(1056, 84, 148, 60, "RO skid")
    c.state_circle(1076, 128, 6, "UTL.RO.YS01.PV")
    c.label(1088, 132, "running", size=10, color=INK)
    row(c, 1056, 180, "Product", "UTL.RO.FT01.PV", 1, "gal/min", 120)
    row(c, 1056, 204, "Conductivity", "UTL.RO.AT01.PV", 0, "µS/cm", 120)

    c.card(20, 388, 360, 416, "Wastewater neutralization")
    c.rect(40, 440, 200, 150, radius=3)
    c.fill("M40 440 h200 v150 h-200 Z", 40, 440, 200, 150, "UTL.WWN.LT01.PV", 0, 100, MEDIA["cip"])
    c.rect(40, 440, 200, 150, fill="none", radius=3)
    c.label(140, 464, "Pit", size=12, color=INK, anchor="middle", bold=True)
    c.number(150, 510, "UTL.WWN.LT01.PV", digits=0, size=22)
    c.label(152, 510, "%", size=11, color=INK)
    c.flags(250, 444, "UTL.WWN.LT01.PV", down=True)
    status(c, 250, 520, "High level", "UTL.WWN.LSH01.PV")
    c.label(40, 626, "pH (the band is what the sewer permit allows)", size=10)
    c.hbar(40, 640, 300, 12, "UTL.WWN.AT01.PV", 0, 14, normal=(6, 9), color=MEDIA["cip"])
    c.number(80, 680, "UTL.WWN.AT01.PV", digits=1, size=18)
    c.flags(90, 666, "UTL.WWN.AT01.PV")
    for k, (cap, tag) in enumerate((("Caustic dose", "UTL.WWN.XV01.CMD"), ("Acid dose", "UTL.WWN.XV02.CMD"),
                                    ("Discharge", "UTL.WWN.XV03.CMD"))):
        status(c, 40 + k * 110, 730, cap, tag)
    status(c, 40, 768, "Discharge inhibited", "UTL.WWN.XS01.INHIBIT")

    c.card(390, 388, 830, 416, "Clean in place (CIP)")
    c.word(406, 440, "UTL.CIP.XS01.STEP", CIP_STEPS, size=22)
    for n in range(1, 8):
        x: float = 620 + (n - 1) * 84
        if n < 7:
            c.path(f"M{x + 9:g} 432 L{x + 75:g} 432", stroke=RULE, width=2)
        c.step_lamp(x, 432, "UTL.CIP.XS01.STEP", n, r=7)
        c.label(x, 456, CIP_STEPS[n], size=9, anchor="middle")
    c.pipe_run([(460, 620), (460, 648), (1200, 648)], "cip", 5, flow="UTL.CIP.HS01.CMD")
    c.pipe_run([(1200, 700), (406, 700)], "cip", 5, flow="UTL.CIP.HS02.CMD")
    for k, (name, tag, medium) in enumerate((("Caustic", "UTL.CIP.LT01.PV", "cip"), ("Acid/sanitizer", "UTL.CIP.LT02.PV", "hot"))):
        x = 420 + k * 110
        vessel(c, x, 500, 80, 120, tag, medium)
        c.label(x + 40, 492, name, size=11, color=INK, anchor="middle", bold=True)
        c.number(x + 50, 566, tag, digits=0, size=16)
        c.label(x + 52, 566, "%", size=10, color=INK)
    c.pump(640, 648, "UTL.CIP.HS01.CMD")
    c.label(640, 676, "supply pump", size=9, anchor="middle")
    c.pump(720, 700, "UTL.CIP.HS02.CMD", facing="left")
    c.label(720, 728, "return pump", size=9, anchor="middle")
    c.label(1196, 636, "to the vessel", size=9, anchor="end")
    c.label(1196, 722, "back from the vessel", size=9, anchor="end")
    c.gauge(680, 510, 12, 90, "UTL.CIP.TT01.PV", 60, 200, normal=(150, 170), digits=0, unit="°F", caption="Supply",
            setpoint="UTL.CIP.TT01.SP")
    c.gauge(760, 510, 12, 90, "UTL.CIP.AT01.PV", 0, 100, digits=0, unit="mS/cm", caption="Return")
    c.gauge(840, 510, 12, 90, "UTL.CIP.FT01.PV", 0, 100, digits=0, unit="gal/min", caption="Return flow")
    status(c, 930, 516, "Return flow proven", "UTL.CIP.FS01.PV")
    status(c, 930, 540, "Route verified", "UTL.CIP.XS02.ROUTE_OK")
    status(c, 930, 564, "Cellar permits CIP", "UTL.CIP.XS04.PV")
    status(c, 930, 588, "Transfer in progress", "UTL.CIP.XS03.PV")
    c.label(406, 762, "Circuit", size=11)
    c.entry(450, 747, "UTL.CIP.XC01.CIRCUIT", 0, 8, w=36, digits=0)
    for k, (text, value) in enumerate((("Start", 1), ("Hold", 2), ("Resume", 0), ("Abort", 3))):
        c.button(496 + k * 74, 744, 68, 26, text, "UTL.CIP.XC01.CMD", value)
    c.label(806, 762, "swing panel on:", size=10)
    for k, name in enumerate(("1 Brewhouse", "2 FV-101", "3 FV-102", "4 FV-103", "5 FV-104", "6 BBT-201", "7 BBT-202")):
        x = 420 + k * 114
        c.state_circle(x + 6, 784, 5, f"UTL.CIP.ZS0{k + 1}.PV")
        c.label(x + 16, 788, name, size=10, color=INK)
    return c.screen(VIEW_ID["Utilities"])


# ---------------------------------------------------------------- power


def power(plant: Plant) -> Screen:
    """The one-line diagram: the utility on the left, the switchboard as a bar,
    the sources on top of it and the loads under it, the emergency panel on the
    right behind its transfer switch. A conductor is dark while it is live."""
    c: Canvas = canvas("Power", plant)
    c.title(20, 30, "Power")
    bus: float = 300
    main: str = "PWR.MAIN.ZS01.PV"

    # -- utility service and the bus
    c.label(30, 250, "Summit Valley Power", size=11, color=INK, bold=True)
    c.label(30, 266, "12.47 kV, T-1 500 kVA", size=10)
    c.wire([(30, bus), (158, bus)])
    c.wire([(182, bus), (1010, bus)], main, width=8)
    c.switch(170, bus, main, r=10)
    c.label(170, bus + 30, "main 52-M", size=10, anchor="middle")
    c.flags(163, bus + 36, "PWR.MAIN.YA01.PV")
    c.label(600, bus - 12, "480 V main switchboard", size=11, color=INK, anchor="middle", bold=True)
    c.card(20, 46, 300, 180, "Service")
    c.number(140, 96, "PWR.MAIN.JT01.PV", digits=0, size=30)
    c.label(144, 96, "kW import", size=11)
    c.flags(240, 80, "PWR.MAIN.JT01.PV")
    row(c, 36, 126, "Reactive", "PWR.MAIN.JT02.PV", 0, "kvar", 130)
    row(c, 36, 150, "Voltage", "PWR.MAIN.ET01.PV", 0, "V", 130)
    row(c, 36, 174, "Current", "PWR.MAIN.IT01.PV", 0, "A", 130)
    row(c, 36, 198, "Frequency", "PWR.MAIN.ST01.PV", 2, "Hz", 130)

    # -- sources on top of the bus
    for i, (name, kw, sw) in enumerate((("Rooftop PV", "PWR.PV.JT01.PV", "PWR.PV.ZS01.PV"),
                                        ("Battery", "PWR.BESS.JT01.PV", "PWR.BESS.ZS01.PV"))):
        x: float = 440 + i * 240
        c.card(x - 100, 46, 200, 180, name)
        c.number(x + 20, 96, kw, digits=0, size=26)
        c.label(x + 24, 96, "kW", size=11)
        c.wire([(x, 226), (x, bus)], sw)
        c.switch(x, 262, sw)
    row(c, 356, 130, "Output limit", "PWR.PV.JY01.PV", 0, "%", 150)
    c.hbar(356, 146, 170, 8, "PWR.PV.JY01.PV", 0, 100, color=MEDIA["beer"])
    row(c, 356, 184, "Site limit", "PWR.PV.JC01.SP", 0, "%", 150)
    row(c, 356, 206, "Utility limit", "PWR.PV.JC02.SP", 0, "%", 150)
    c.label(596, 124, "charge", size=10)
    c.number(766, 124, "PWR.BESS.EY01.PV", digits=0, size=12)
    c.hbar(596, 130, 170, 12, "PWR.BESS.EY01.PV", 0, 100, normal=(50, 100), color=LIVE)
    c.flags(772, 128, "PWR.BESS.EY01.PV")
    row(c, 596, 170, "Cell temperature", "PWR.BESS.TT01.PV", 0, "°F", 150)
    row(c, 596, 192, "Off-gas", "PWR.BESS.AT01.PV", 0, "ppm", 150)
    status(c, 596, 214, "Fault", "PWR.BESS.YA01.PV")

    # -- demand
    c.card(820, 46, 400, 180, "Demand")
    c.label(840, 84, "15-minute demand, kW (the band is the demand target)", size=10)
    c.hbar(840, 98, 360, 14, "PWR.MAIN.JQ01.PV", 0, 400, normal=(0, 250))
    c.number(900, 138, "PWR.MAIN.JQ01.PV", digits=0, size=18)
    c.flags(908, 124, "PWR.MAIN.JQ01.PV")
    c.label(950, 138, "Target kW", size=11)
    c.entry(1006, 123, "PWR.DMD.JY01.SP", 0, 1000, w=50, digits=0)
    row(c, 1070, 138, "Month peak", "PWR.DMD.JY02.PV", 0, "kW", 120)
    status(c, 840, 172, "On-peak", "PWR.DMD.XS01.ONPEAK")
    status(c, 940, 172, "Load shed", "PWR.DMD.XS02.SHED")
    status(c, 1050, 172, "Brew day", "PWR.DMD.XS03.BREWDAY")
    c.button(1128, 156, 40, 24, "Set", "PWR.DMD.XS03.BREWDAY", 1)
    c.button(1170, 156, 46, 24, "Clear", "PWR.DMD.XS03.BREWDAY", 0)
    status(c, 840, 200, "Generator exercise", "PWR.DMD.XS04.EXERCISE")
    c.label(1160, 205, "Brew-day battery reserve %", size=9, anchor="end")
    c.entry(1166, 190, "PWR.DMD.EY01.SP", 0, 100, w=46, digits=0)
    status(c, 960, 200, "Clock ok", "PWR.DMD.XS05.CLKOK")

    # -- feeders under the bus
    for i, (name, sw, cmd) in enumerate((("MCC-1 process", "", ""), ("Boiler", "PWR.MAIN.ZS02.PV", "PWR.MAIN.HS02.CMD"),
                                         ("Packaging", "PWR.MAIN.ZS03.PV", "PWR.MAIN.HS03.CMD"),
                                         ("RTU-1", "PWR.MAIN.ZS04.PV", "PWR.MAIN.HS04.CMD"),
                                         ("Lighting LP-1", "", ""))):
        x = 260 + i * 150
        c.wire([(x, bus), (x, 400)], sw or main, width=3)
        if sw:
            c.switch(x, 350, sw)
        c.rect(x - 56, 400, 112, 40, radius=4)
        c.label(x, 424, name, size=11, color=INK, anchor="middle", bold=True)
        if cmd:
            c.state_circle(x + 20, 350, 4, cmd)
            c.label(x + 28, 354, "close command", size=9)

    # -- the transfer switch, the generator, the emergency panel
    c.wire([(1010, bus), (1060, bus), (1060, 380)], main)
    c.rect(1010, 380, 150, 60, radius=4)
    c.label(1085, 398, "Transfer switch", size=11, color=INK, anchor="middle", bold=True)
    status(c, 1022, 418, "utility available", "PWR.GEN.ZS02.PV")
    status(c, 1022, 434, "on generator", "PWR.GEN.ZS01.PV")
    c.wire([(1085, 440), (1085, 500)])
    c.rect(1010, 500, 150, 50, radius=4)
    c.label(1085, 520, "Emergency panel EP-1", size=11, color=INK, anchor="middle", bold=True)
    c.number(1090, 540, "PWR.GEN.JT02.PV", digits=0, size=13)
    c.label(1092, 540, "kW", size=9)
    c.wire([(1160, 410), (1200, 410), (1200, 600)], "PWR.GEN.YS01.PV")
    c.card(1010, 600, 210, 204, "Generator")
    c.state_circle(1040, 640, 10, "PWR.GEN.YS01.PV")
    c.label(1058, 644, "running", size=11, color=INK)
    c.number(1150, 684, "PWR.GEN.JT01.PV", digits=0, size=22)
    c.label(1154, 684, "kW", size=10)
    c.label(1030, 712, "fuel %", size=10)
    c.number(1200, 712, "PWR.GEN.LT01.PV", digits=0, size=12)
    c.hbar(1030, 718, 170, 12, "PWR.GEN.LT01.PV", 0, 100, normal=(25, 100), color=MEDIA["hot"])
    c.flags(1030, 742, "PWR.GEN.LT01.PV")
    status(c, 1030, 784, "Remote start (exercise)", "PWR.GEN.HS01.CMD")

    c.trend(20, 470, 480, 334, "Sources kW, last hour", [
        ("PWR.MAIN.JT01.PV", "Import", INK),
        ("PWR.PV.JT01.PV", "PV", MEDIA["beer"]),
        ("PWR.BESS.JT01.PV", "Battery", LIVE),
        ("PWR.GEN.JT01.PV", "Generator", MEDIA["hot"]),
    ])
    c.trend(510, 470, 480, 334, "Demand and charge, last hour", [
        ("PWR.MAIN.JQ01.PV", "Demand kW", INK),
        ("PWR.BESS.EY01.PV", "Charge %", LIVE),
    ])
    return c.screen(VIEW_ID["Power"])


# ---------------------------------------------------------------- CO2 and the building


def _co2_room(c: Canvas, x: float, y: float) -> None:
    """The cellar as a room seen from above: the vessels, the four CO2 sensors
    where they hang, and the purge fans in the wall."""
    c.rect(x, y, 700, 330, fill=WHITE, stroke=INK, width=3)
    c.label(x + 4, y - 8, "Cellar, from above. CO2 is heavier than air: it pools at the floor.", size=10)
    for i in range(4):
        c.path(f"M{x + 90 + i * 100:g} {y + 80:g} m-36 0 a36 36 0 1 0 72 0 a36 36 0 1 0 -72 0", fill=BODY)
        c.label(x + 90 + i * 100, y + 84, f"FV-10{i + 1}", size=10, color=INK, anchor="middle")
    for j in range(2):
        c.path(f"M{x + 540 + j * 80:g} {y + 80:g} m-30 0 a30 30 0 1 0 60 0 a30 30 0 1 0 -60 0", fill=BODY)
        c.label(x + 540 + j * 80, y + 84, f"BBT-20{j + 1}", size=9, color=INK, anchor="middle")
    for i, (sx, sy) in enumerate(((90, 200), (290, 200), (490, 200), (290, 286))):
        tag: str = f"FAC.VENT.AT0{i + 1}.PV"
        c.rect(x + sx - 62, y + sy - 30, 124, 56, fill="#FBFBFC", stroke=RULE, width=1, radius=4)
        c.label(x + sx - 54, y + sy - 14, f"CO2 sensor {i + 1}", size=9)
        c.number(x + sx + 4, y + sy + 12, tag, digits=0, size=20)
        c.label(x + sx + 7, y + sy + 12, "ppm", size=9)
        c.flags(x + sx + 30, y + sy - 26, tag, step=16)
    c.label(x + 690, y + 150, "wall", size=9, anchor="end")
    for k, fy in enumerate((y + 200, y + 280)):
        c.fan(x + 700, fy, f"FAC.VENT.FAN0{k + 1}.PV", r=16)
        c.label(x + 724, fy - 8, f"purge fan {k + 1}", size=10, color=INK)
        c.arrow(x + 760, fy + 12, "right", color=DIM)


def co2(plant: Plant) -> Screen:
    """Cellar CO2, as SCADA reads it from the building's controllers: read only."""
    c: Canvas = canvas("Cellar CO2", plant)
    c.title(20, 30, "Cellar CO2")
    c.label(140, 30, "life safety, read from the building system; this screen operates nothing", size=12)
    _co2_room(c, 30, 80)
    c.card(880, 60, 340, 350, "Alarm tiers")
    for k, (text, tag) in enumerate((("Tier 1: 5,000 ppm, purge fans on", "FAC.VENT.AY01.TIER1"),
                                     ("Tier 2: 15,000 ppm, carbonation inhibited", "FAC.VENT.AY01.TIER2"),
                                     ("Tier 3: 30,000 ppm, evacuate; CO2 supply shut off", "FAC.VENT.AY01.TIER3"))):
        status(c, 896, 100 + k * 30, text, tag, r=6)
    status(c, 896, 206, "Tier 1 at the bulk tank, filler, or cold room", "FAC.VENT.AY02.TIER1")
    status(c, 896, 234, "Horns and strobes", "FAC.VENT.XA01.CMD")
    status(c, 896, 262, "CO2 shutoff relay energized (healthy)", "FAC.VENT.XS01.SHUTOFF_OK")
    c.label(896, 304, "CO2 in the other areas", size=10)
    for k, name in enumerate(("Bulk CO2 tank", "Filler", "Cold room")):
        row(c, 896, 330 + k * 24, name, f"FAC.VENT.AT0{k + 5}.PV", 0, "ppm", 190)
    c.trend(30, 440, 1190, 364, "CO2 at the four cellar sensors, ppm, last hour", [
        (f"FAC.VENT.AT0{i + 1}.PV", f"Sensor {i + 1}", color) for i, color in enumerate(FV_COLORS)
    ])
    return c.screen(VIEW_ID["Cellar CO2"])


def building(plant: Plant) -> Screen:
    """bas-fuxa: the building as a plan, with each controller in its room."""
    c: Canvas = canvas("Building", plant)
    c.title(20, 30, "Building")
    c.label(124, 30, "taproom rooftop unit, cold room, and ventilation", size=12)

    # -- taproom and RTU-1
    c.rect(20, 60, 560, 300, fill=WHITE, stroke=INK, width=3)
    c.label(34, 84, "TAPROOM", size=11, bold=True)
    c.number(150, 150, "FAC.RTU1.TT01.PV", digits=1, size=40)
    c.label(154, 150, "°F", size=14)
    c.flags(200, 120, "FAC.RTU1.TT01.PV")
    c.scale(236, 96, 12, 120, "FAC.RTU1.TT01.PV", 50, 90, normal=(68, 76), setpoint="FAC.RTU1.TIC01.SP")
    c.word(40, 180, "FAC.RTU1.HS01.MODE", OCCUPANCY, size=14)
    c.label(40, 214, "Cool to °F", size=11)
    c.entry(120, 199, "FAC.RTU1.TIC01.SP", 60, 90, w=46, digits=0)
    c.label(40, 242, "Heat to °F", size=11)
    c.entry(120, 227, "FAC.RTU1.TIC02.SP", 50, 80, w=46, digits=0)
    row(c, 40, 274, "Room CO2", "FAC.RTU1.AT01.PV", 0, "ppm", 130)
    c.rect(330, 92, 234, 160, radius=6)
    c.label(447, 112, "Rooftop unit RTU-1", size=12, color=INK, anchor="middle", bold=True)
    c.fan(370, 156, "FAC.RTU1.FAN01.CMD", r=16)
    c.label(370, 190, "supply fan", size=9, anchor="middle", color=INK)
    c.state_circle(432, 156, 9, "FAC.RTU1.CMP01.CMD", on=MEDIA["glycol"])
    c.state_circle(460, 156, 9, "FAC.RTU1.CMP02.CMD", on=MEDIA["glycol"])
    c.label(446, 190, "cooling 1, 2", size=9, anchor="middle", color=INK)
    c.state_circle(522, 156, 9, "FAC.RTU1.HTR01.CMD", on=MEDIA["hot"])
    c.label(522, 190, "heat", size=9, anchor="middle", color=INK)
    c.label(346, 222, "Supply air", size=11, color=INK)
    c.number(470, 222, "FAC.RTU1.TT02.PV", digits=1, size=12)
    c.label(473, 222, "°F", size=9, color=INK)
    c.label(346, 240, "Power", size=11, color=INK)
    c.number(470, 240, "FAC.RTU1.JT01.PV", digits=1, size=12)
    c.label(473, 240, "kW", size=9, color=INK)
    c.label(330, 282, "economizer damper %", size=10)
    c.hbar(330, 290, 190, 10, "FAC.RTU1.DMP01.CMD", 0, 100, color=MEDIA["air"])
    c.number(556, 300, "FAC.RTU1.DMP01.CMD", digits=0, size=12)

    # -- cold room
    c.rect(600, 60, 300, 300, fill=WHITE, stroke=INK, width=3)
    c.label(614, 84, "COLD ROOM", size=11, bold=True)
    c.number(740, 150, "FAC.CR.TT01.PV", digits=1, size=40)
    c.label(744, 150, "°F", size=14)
    c.flags(790, 120, "FAC.CR.TT01.PV")
    c.scale(840, 96, 12, 120, "FAC.CR.TT01.PV", 28, 50, normal=(33, 38), setpoint="FAC.CR.TT01.SP")
    c.label(620, 196, "Hold at °F", size=11)
    c.entry(690, 181, "FAC.CR.TT01.SP", 28, 50, w=46, digits=0)
    row(c, 620, 232, "Door open today", "FAC.CR.KQ01.PV", 0, "min", 170)
    status(c, 620, 270, "Door open", "FAC.CR.ZS01.PV")
    status(c, 620, 298, "Compressor", "FAC.CR.CMP01.CMD")
    status(c, 620, 326, "Defrost", "FAC.CR.DEF01.CMD")

    # -- ventilation: the hood, the makeup air, the area exhaust
    c.rect(920, 60, 300, 300, fill=WHITE, stroke=INK, width=3)
    c.label(934, 84, "BREWHOUSE AND OTHER AREAS", size=11, bold=True)
    c.fan(960, 130, "FAC.VENT.SC01.CMD", r=16, above=1)
    c.label(990, 118, "kettle hood exhaust %", size=10)
    c.hbar(990, 126, 160, 10, "FAC.VENT.SC01.CMD", 0, 100, color=MEDIA["air"])
    c.number(1196, 136, "FAC.VENT.SC01.CMD", digits=0, size=12)
    c.fan(960, 186, "FAC.VENT.MUA01.CMD", r=12)
    c.label(986, 190, "Makeup air unit", size=11, color=INK)
    c.fan(960, 222, "FAC.VENT.FAN03.CMD", r=12)
    c.label(986, 226, "Area exhaust fans", size=11, color=INK)
    row(c, 940, 266, "Makeup air discharge", "FAC.VENT.TT01.PV", 1, "°F", 200)
    for k, name in enumerate(("Bulk CO2 tank", "Filler", "Cold room")):
        row(c, 940, 292 + k * 22, f"CO2 at the {name[0].lower()}{name[1:]}", f"FAC.VENT.AT0{k + 5}.PV", 0, "ppm", 200)

    # -- the cellar, with its sensors and fans
    _co2_room(c, 20, 404)
    c.card(880, 380, 340, 424, "Cellar ventilation")
    for k, (text, tag) in enumerate((("Tier 1: 5,000 ppm, purge fans on", "FAC.VENT.AY01.TIER1"),
                                     ("Tier 2: 15,000 ppm, carbonation inhibited", "FAC.VENT.AY01.TIER2"),
                                     ("Tier 3: 30,000 ppm, evacuate; CO2 supply shut off", "FAC.VENT.AY01.TIER3"))):
        status(c, 896, 420 + k * 28, text, tag, r=6)
    status(c, 896, 518, "Tier 1 at the bulk tank, filler, or cold room", "FAC.VENT.AY02.TIER1")
    status(c, 896, 546, "CO2 shutoff relay energized (healthy)", "FAC.VENT.XS01.SHUTOFF_OK")
    status(c, 896, 574, "Horns and strobes", "FAC.VENT.XA01.CMD")
    c.button(1070, 558, 62, 26, "Sound", "FAC.VENT.XA01.CMD", 1)
    c.button(1138, 558, 62, 26, "Silence", "FAC.VENT.XA01.CMD", 0)
    for k in range(2):
        y: float = 626 + k * 40
        c.label(896, y, f"Purge fan {k + 1}", size=11, color=INK)
        c.button(990, y - 18, 62, 26, "Run", f"FAC.VENT.FAN0{k + 1}.CMD", 1)
        c.button(1058, y - 18, 62, 26, "Stop", f"FAC.VENT.FAN0{k + 1}.CMD", 0)
        c.state_circle(1140, y - 5, 6, f"FAC.VENT.FAN0{k + 1}.PV")
        c.label(1152, y, "running", size=10, color=INK)
    return c.screen(VIEW_ID["Building"])


def screens(plant: Plant) -> list[Screen]:
    """The process graphics a host's tags allow, in menu order: the whole plant
    for scada-fuxa, the Cellar alone for the ci plant, the Building for bas-fuxa."""
    names: dict[str, PlantTag] = plant.tags
    if "FAC.RTU1.TT01.PV" in names:
        return [building(plant)]
    if "BH.MLT.TT01.PV" in names:
        return [overview(plant), brewhouse(plant), cellar(plant), packaging(plant), utilities(plant),
                power(plant), co2(plant)]
    if "CEL.FV101.TT01.PV" in names:
        return [cellar(plant)]
    return []
