"""Write the values the stand-in plant serves to FUXA for one scenario.

It runs the real federates offline (``LocalSource``, no HELICS) to a moment of
a scenario, and writes a JSON file of engineering values by FUXA tag name:

- **sim**: every tag whose source is ``io`` takes the value its ``helics_key``
  has in the federation (the same value rio-* hands the PLC).
- **profile**: the fermentation profile of plc-cellar, which the PLC computes and
  this file stages from the offline cellar's state. ``CEL.PROFILE.HSnn.AUTO`` is 1
  (profile on) for every fermenter; the stage ``CEL.PROFILE.KSnn.STAGE`` is 0 when the
  fermenter's apparent extract is under 0.5 degP (empty), 1 at 5 degP or more, 3 under
  5 degP with the side temperature under 60 degF, and 2 otherwise; the profile's
  setpoints are 66, 70, and 34 degF and 180 min (the program's starting values); and
  each fermenter's ``TT01.SP`` and ``TT02.SP`` are what the PLC writes with the
  profile on: 66 for stage 0 or 1, 70 for stage 2, 34 for stage 3.
- **autopilot**: the bright tanks' temperature and carbonation setpoints
  (``CEL.BBTnnn.TT01.SP``, ``CEL.BBTnnn.AT01.SP``) are the targets the offline cellar
  federate's built-in operator holds (``Federate._autopilot``).
- **program**: a setpoint the PLC program initializes (``NAME_SP := n;`` in
  ``plc/*/*.st``) takes that number divided by the tag's divisor.
- **alarm**: a PLC alarm bit (``source: plc`` in plantgen's tag list) of kind high,
  highhigh, or state is 1 when its alarm's source value is past the limit in
  ``alarms.json``. The PLC's on-delay and deadband are not applied, and the low and
  deviation alarms are not staged (they depend on the PLC's delays and on what
  its controller is doing), so they read 0.
- every other tag the PLC computes (states, counters, permits) is not in the
  file, and the stand-in serves 0 for it.

    python fuxa_snapshot.py SCENARIO OUT.json [--root DIR]

Scenarios are the viz scenarios (none, co2-leak, co2-attack, spunding-attack,
outage) and the ones below. ``--root`` is the ``cherrymountain-brewery``
directory (CM_ROOT, else this file's place in the tree). Needs the repo's sim and
viz packages importable.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT: Path = Path(os.environ.get("CM_ROOT") or Path(__file__).resolve().parents[3])
sys.path[:0] = [str(ROOT / "viz"), str(ROOT / "sim")]

from cherrymountain_sim.federate import AUTO_BBT_F, AUTO_CARB_VOL  # noqa: E402
from cherrymountain_viz.scenarios import SCENARIOS, Step  # noqa: E402
from cherrymountain_viz.sources import LocalSource  # noqa: E402

MIDDAY_S: float = 6.5 * 3600  # 04:30 start + 6.5 h = 11:00
PACKAGING_S: float = 3.5 * 3600  # 08:00

# name: (run to this federate second, scenario steps)
EXTRA: dict[str, tuple[float, tuple[Step, ...]]] = {
    "midday": (MIDDAY_S, ()),
    # 08:00: the cellar crew has put beer in BBT-202 and the packaging line runs.
    "packaging-run": (PACKAGING_S, ()),
    "packaging-jam": (
        PACKAGING_S,
        tuple(
            Step(
                at_s=PACKAGING_S - 400.0,
                kind="event",
                federate="packaging",
                payload={"type": "jam", "params": {"machine": m, "duration": 1200}},
                note=f"jam {m} (SCORCH packaging-shift)",
            )
            for m in ("labeler", "filler")
        ),
    ),
    "brew-day": (
        1.5 * 3600,
        (
            Step(
                at_s=30.0,
                kind="event",
                federate="brewhouse",
                payload={"type": "start-batch", "params": {"brews": 1}},
                note="start-batch brews=1 (SCORCH baseline-brew-day)",
            ),
        ),
    ),
}
RUN_TO: dict[str, float] = {
    "none": 900.0,
    "co2-leak": 1500.0,
    "co2-attack": 1500.0,
    "spunding-attack": 3430.0,
    "outage": MIDDAY_S,
}


def program_setpoints() -> dict[str, int]:
    """First ``NAME_SP := n;`` of each PLC program: ST variable -> raw value."""
    out: dict[str, int] = {}
    for path in sorted((ROOT / "plc").glob("*/*.st")):
        if path.name == "plc-cellar.st":  # the ci program; the plant one is the full variant's
            continue
        for m in re.finditer(r"^\s*([A-Z0-9_]+_SP)\s*:=\s*(-?\d+)\s*;", path.read_text(), re.M):
            tag = m.group(1)
            out.setdefault(tag, int(m.group(2)))
    return out


def main() -> int:
    """Run the scenario and write the values."""
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("scenario")
    ap.add_argument("out", type=Path)
    args = ap.parse_args()

    name = args.scenario
    if name in EXTRA:
        run_to, steps = EXTRA[name]
        SCENARIOS[name] = steps
    else:
        run_to = RUN_TO[name]
    plant = ROOT / "plant" / "generated" / "full"
    src = LocalSource(
        plant / "helics.json",
        scenario=name,
        fast_forward_s=run_to,
        scenario_at_s=(MIDDAY_S - 120.0 if name == "outage" else 0.0),
    )
    values = src.values()
    tags = {t["name"]: t for t in json.loads((plant / "tags.json").read_text())["tags"]}
    divisor: dict[str, float] = {}
    for dev in json.loads((plant / "fuxa" / "scada-fuxa-tags.json").read_text())["devices"]:
        for t in dev["tags"]:
            divisor[t["name"]] = float(t.get("divisor") or 1)
    for dev in json.loads((plant / "fuxa" / "bas-fuxa-tags.json").read_text())["devices"]:
        for t in dev["tags"]:
            divisor.setdefault(t["name"], float(t.get("divisor") or 1))

    eng: dict[str, Any] = {}
    kind: dict[str, str] = {}
    for tname, t in tags.items():
        key = t.get("helics_key")
        if t["source"] == "io" and key in values:
            v = values[key]
            eng[tname] = (1 if v else 0) if isinstance(v, bool) else float(v)
            kind[tname] = "sim"
    cellar = src.fed.feds["cellar"]
    for n in (1, 2, 3, 4):
        extract = eng.get(f"CEL.FV10{n}.DT01.PV", 0.0)
        side = eng.get(f"CEL.FV10{n}.TT01.PV", 0.0)
        stage = 0 if extract < 0.5 else (1 if extract >= 5.0 else (3 if side < 60.0 else 2))
        staged = {
            f"CEL.PROFILE.HS0{n}.AUTO": 1,
            f"CEL.PROFILE.KS0{n}.STAGE": stage,
            f"CEL.FV10{n}.TT01.SP": (66.0, 66.0, 70.0, 34.0)[stage],
            f"CEL.FV10{n}.TT02.SP": (66.0, 66.0, 70.0, 34.0)[stage],
        }
        for key, value in staged.items():
            eng[key] = value
            kind[key] = "profile"
    for key, value in (
        ("CEL.PROFILE.TC01.SP", 66.0),
        ("CEL.PROFILE.TC02.SP", 70.0),
        ("CEL.PROFILE.TC03.SP", 34.0),
        ("CEL.PROFILE.KC01.SP", 180.0),
    ):
        eng[key] = value
        kind[key] = "profile"
    for unit in cellar.cellar.bbts:  # "BBT201"
        for point, value in (("TT01", AUTO_BBT_F), ("AT01", AUTO_CARB_VOL)):
            if f"CEL.{unit}.{point}.SP" in tags:
                eng[f"CEL.{unit}.{point}.SP"] = value
                kind[f"CEL.{unit}.{point}.SP"] = "autopilot"
    by_var = {n.replace(".", "_"): n for n in tags}
    for var, raw in program_setpoints().items():
        tname = by_var.get(var, "")
        if tname and tname not in eng:
            eng[tname] = raw / divisor.get(tname, 1.0)
            kind[tname] = "program"
    alarms = json.loads((plant / "alarms.json").read_text())["alarms"]
    for a in alarms:
        bit = a["name"]
        if bit in eng or bit not in tags or a["source"] not in eng:
            continue
        pv = eng[a["source"]]
        lim = float(a["limit"])
        match a["kind"]:
            case "high" | "highhigh":
                on = pv > lim
            case "state":
                on = pv >= lim
            case _:
                continue
        eng[bit] = int(on)
        kind[bit] = "alarm"
    args.out.write_text(
        json.dumps(
            {
                "scenario": name,
                "sim_time": src.fed.feds["weather"].clock.at(src.fed.t).isoformat(),
                "federate_s": src.fed.t,
                "notes": src.notes,
                "values": eng,
                "kind": kind,
            },
            indent=0,
            sort_keys=True,
        )
    )
    counts = {k: sum(1 for v in kind.values() if v == k) for k in ("sim", "profile", "autopilot", "program", "alarm")}
    print(f"{name}: {len(eng)} of {len(tags)} tags, {counts}, at {src.fed.t:.0f} s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
