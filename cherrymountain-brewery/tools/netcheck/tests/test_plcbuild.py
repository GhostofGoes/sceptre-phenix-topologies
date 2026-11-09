"""plc/common/plcbuild.py, and the constants the PLC programs share with the
simulation (the recipe, the mill, the oxygen dosing, the level switch)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT: Path = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plc" / "common"))
sys.path.insert(0, str(ROOT / "sim"))

import plcbuild  # noqa: E402 - a sibling script, not a package
from cherrymountain_sim import brewhouse  # noqa: E402
from cherrymountain_sim.federate import HelicsConfig, build  # noqa: E402
from cherrymountain_sim.recipe import default as default_recipe  # noqa: E402

PLCS: tuple[str, ...] = ("plc-brewhouse", "plc-cellar", "plc-packaging", "plc-utilities", "plc-power")
BREWHOUSE: str = (ROOT / "plc" / "plc-brewhouse" / "plc-brewhouse.st").read_text(encoding="utf-8")
#: Mill throughput at the 50 Hz the MILL phase commands (BrewhouseFederate._mill).
MILL_KG_PER_S: float = 0.6 * 50.0 / 60.0
#: mg of O2 per standard liter (BrewhouseFederate._knockout) and the uptake.
O2_MG_PER_L: float = 1430.0
O2_UPTAKE: float = 0.6
LITERS_PER_GALLON: float = 3.785411784


def constant(name: str) -> int:
    """An integer ``VAR CONSTANT`` of the brewhouse program."""
    m: re.Match[str] | None = re.search(rf"^\s*{name}\s*:\s*\w+\s*:=\s*(\d+)\s*;", BREWHOUSE, re.MULTILINE)
    assert m is not None, f"{name} is not a constant of plc-brewhouse.st"
    return int(m.group(1))


@pytest.mark.parametrize("variant", plcbuild.VARIANTS)
@pytest.mark.parametrize("plc", PLCS)
def test_every_program_assembles_clean(plc: str, variant: str) -> None:
    full, problems = plcbuild.assemble(plc, variant)
    assert problems == []
    assert f"TASK task0(INTERVAL := T#{plcbuild.SCAN_MS}ms" in full


def test_a_scan_count_must_use_the_task_interval() -> None:
    source: str = BREWHOUSE.replace(f"SCAN_MS : UDINT := {plcbuild.SCAN_MS};", "SCAN_MS : UDINT := 250;")
    assert source != BREWHOUSE
    problems: list[str] = plcbuild.check(source, {}, {}, [])
    assert problems == [f"SCAN_MS is 250, but the task interval is {plcbuild.SCAN_MS} ms"]
    assert plcbuild.check(BREWHOUSE, {}, {}, []) == []


def test_the_mill_phase_mills_the_grain_bill() -> None:
    """The mill has no weigh cell, so the MILL phase is a time: at the
    simulated mill's throughput it must deliver the recipe's grain, and not
    more than the mash tun takes."""
    milled_kg: float = constant("MILL_MS") / 1000.0 * MILL_KG_PER_S
    grain_kg: float = default_recipe().grain_kg
    assert grain_kg <= milled_kg <= 1.05 * grain_kg


def test_knockout_oxygen_lands_in_the_target_band() -> None:
    """FC01.SP (L/min x 100) is ratioed to FT01.PV (gal/min x 10); the wort
    must leave the heat exchanger at 8 to 10 ppm dissolved oxygen (DESIGN.md 4)."""
    m: re.Match[str] | None = re.search(r"BH_KO_FC01_SP := MIN\(BH_KO_FT01_PV \* (\d+) / (\d+), 1000\);", BREWHOUSE)
    assert m is not None
    slpm_per_gpm: float = int(m.group(1)) / int(m.group(2)) * 10.0 / 100.0
    ppm: float = O2_UPTAKE * O2_MG_PER_L * slpm_per_gpm / LITERS_PER_GALLON
    assert 8.0 <= ppm <= 10.0


def test_hlt_steam_needs_the_low_level_switch_made() -> None:
    """BH.HLT.LSL01 is made while the liquor covers the steam jacket and drops
    below 15 %, in the federate and in the program alike: steam only while it
    is true, so a dead input gives no steam. (The federate once published the
    opposite sense, and the PLC never heated the tank.)"""
    fed = build(HelicsConfig.load(plcbuild.GENERATED / "full" / "helics.json"), "brewhouse")
    assert isinstance(fed, brewhouse.BrewhouseFederate)
    fed.proc.hlt.liters = 0.05 * fed.proc.hlt.capacity_l
    assert fed.values()["brewhouse/hlt.low_level"] is False
    fed.proc.hlt.liters = 0.50 * fed.proc.hlt.capacity_l
    assert fed.values()["brewhouse/hlt.low_level"] is True
    assert "IF BH_HLT_LSL01_PV THEN\n    BH_HLT_TV01_OUT := PValve(" in BREWHOUSE
    assert not re.search(r"IF NOT BH_HLT_LSL01_PV THEN\s+BH_HLT_TV01_OUT := PValve\(", BREWHOUSE)


@pytest.mark.parametrize("variant", plcbuild.VARIANTS)
def test_a_packaging_command_outlasts_two_polls_of_its_rio(variant: str) -> None:
    """The modbus_master plugin samples the outputs once per poll, out of step
    with the scan, so a command pulse must span at least two polls or it can
    fall between them and never reach the machines."""
    source: str = (ROOT / "plc" / "plc-packaging" / "plc-packaging.st").read_text(encoding="utf-8")
    m: re.Match[str] | None = re.search(r"^\s*CMD_SCANS\s*:\s*UINT\s*:=\s*(\d+)\s*;", source, re.MULTILINE)
    assert m is not None and "cmd_scans := CMD_SCANS;" in source
    master: list[dict[str, Any]] = json.loads(
        (plcbuild.GENERATED / variant / "openplc" / "plc-packaging" / "modbus_master.json").read_text()
    )
    polls: list[int] = [
        p["cycle_time_ms"] for dev in master for p in dev["config"]["io_points"] if p["fc"] == 16
    ]
    assert polls, "plc-packaging writes holding registers to its RIO"
    assert int(m.group(1)) * plcbuild.SCAN_MS >= 2 * max(polls)


def test_a_refused_cip_start_is_dropped() -> None:
    """A CIP start that is not taken (a wash in progress, or no verified route)
    must not stay in XC01.CMD, or the wash starts by itself later."""
    source: str = (ROOT / "plc" / "plc-utilities" / "plc-utilities.st").read_text(encoding="utf-8")
    assert re.search(
        r"ELSIF UTL_CIP_XC01_CMD = CIP_CMD_START THEN\b.*?"
        r"IF \(cip_step = 0 OR cip_step = CIP_STEP_DONE\) AND UTL_CIP_XS02_ROUTE_OK THEN\s+"
        r"cip_step := 1;\s+END_IF;\s+UTL_CIP_XC01_CMD := 0;\s+END_IF;",
        source,
        re.DOTALL,
    )


def test_timed_steps_keep_their_time_through_a_hold() -> None:
    """No brewhouse step is timed by a TON, which restarts when a hold drops
    its input: the whirlpool would dose hops again and the mill would mill a
    second grain bill."""
    assert not re.search(r"\b(step_timer|hop_timer)\b", BREWHOUSE)
    assert "step_ms := step_ms + SCAN_MS;" in BREWHOUSE
    assert "BH_KTL_XV02_CMD := step_ms < HOP_DOSE_MS;" in BREWHOUSE
