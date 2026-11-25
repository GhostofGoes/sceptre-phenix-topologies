"""The test simulator's port of plc-brewhouse, held to the compiled program.

The batch engine's scenario tests (test_batch_engine.py) run against
``brewhouse_sim.mjs``, a JavaScript port of the program's ISA-88 handshake. A port
that drifts from the Structured Text would let the engine pass tests for a PLC that
does not exist, so this runs the same command traces through the port and through
plc-brewhouse's ST as built (plcbuild.py, strucpp, g++: ``sim/tests/st_engine.py``)
and requires the same state, step, entry alarms, and command register on every 100 ms
scan. The traces cover the timed phases (mill, mash rest, vorlauf, whirlpool) with
their parameter and default, a hold that keeps the time already served, the condition
phases, Abort and Reset from every state, commands that must be ignored, and the entry
lockout of the mash/lauter tun and the kettle. The boil is not compared: its run time
is the process's (KI01 counts boiling minutes), which the port reduces to a duration.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fuxa_test_support import FUXA_DIR, ROOT, require_node

sys.path.insert(0, str(ROOT / "sim" / "tests"))
import st_engine  # noqa: E402  (sim/tests is not a package)

TRACE: Path = FUXA_DIR / "tests" / "plc_port_trace.mjs"
SCAN_NS: int = 100_000_000  # the task interval (SCAN_MS in the program)

IDLE, RUNNING, HOLDING, HELD, RESTARTING, COMPLETE, ABORTED = range(7)
START, HOLD, RESTART, ABORT, RESET = 1, 2, 3, 4, 5

Segment = dict[str, Any]


def seg(n: int = 1, **fields: Any) -> Segment:
    """``n`` scans; the fields are written before the first (``cmd`` counts for that one only)."""
    return {"n": n, **fields}


def reset(n: int = 2) -> Segment:
    return seg(n, cmd=RESET)


#: Each trace starts with two plain scans: the program loads its defaults on the first.
SCENARIOS: dict[str, list[Segment]] = {
    "cip_ready_completes_at_once": [
        seg(3, phase=10, cmd=START), reset(3), seg(3, cmd=START),  # a start from Complete also runs
        seg(3, cmd=START), reset(),
    ],
    "mill_runs_the_grain_bill": [
        seg(80, phase=1, param=3, cmd=START), reset(),
        seg(10300, param=0, cmd=START), reset(),            # the default: the 503 kg bill, 17 min
    ],
    "parameter_is_latched_at_start": [
        seg(20, phase=1, param=5, cmd=START), seg(200, param=50), reset(),
    ],
    "hold_keeps_the_time_served": [
        seg(40, phase=1, param=5, cmd=START), seg(30, cmd=HOLD), seg(2, cmd=RESTART),
        seg(100, param=50), reset(),
    ],
    "vorlauf_hold_and_restart": [
        seg(100, phase=5, cmd=START), seg(50, cmd=HOLD), seg(5900, cmd=RESTART), reset(),
    ],
    "abort_from_every_state": [
        seg(200, phase=5, cmd=START), seg(10, cmd=ABORT),          # running
        seg(50, cmd=START),                                         # a start from Aborted
        seg(3, cmd=HOLD), seg(3, cmd=ABORT), reset(3),              # held
        seg(20, cmd=START), seg(1, cmd=HOLD), seg(1, cmd=ABORT),    # holding
        reset(),
        seg(20, cmd=START), seg(2, cmd=HOLD), seg(1, cmd=RESTART), seg(1, cmd=ABORT),  # restarting
        reset(), seg(3, cmd=ABORT), reset(),                        # idle: ignored
        seg(1, phase=10, cmd=START), seg(3, cmd=ABORT), reset(),    # complete
    ],
    "mash_rest_counts_at_temperature": [
        seg(500, phase=4, param=2, temp=False, cmd=START), seg(1100, temp=True), seg(150), reset(),
        seg(300, temp=True, cmd=START), seg(400, temp=False), seg(1000, temp=True), reset(),
    ],
    "defaults_run_their_full_time": [
        seg(36100, phase=4, param=0, temp=True, cmd=START), reset(),         # a 60 min rest
        seg(12100, phase=8, cmd=START), reset(),                              # a 20 min whirlpool
    ],
    "whirlpool_by_minutes": [
        seg(200, phase=8, param=1, cmd=START), seg(100, cmd=HOLD), seg(500, cmd=RESTART), seg(50), reset(),
    ],
    "hlt_heat_waits_for_temperature_and_level": [
        seg(30, phase=2, sat=[], cmd=START), seg(3, sat=[2]), reset(),
        seg(3, sat=[2], cmd=START), reset(),                                  # true at the start
    ],
    "mash_in_waits_for_level": [
        seg(30, phase=3, sat=[], cmd=START), seg(20, sat=[2, 6, 9]), seg(3, sat=[3]), reset(),
    ],
    "lauter_waits_for_the_kettle": [
        seg(30, phase=6, sat=[], cmd=START), seg(3, sat=[6]), reset(),
    ],
    "knockout_waits_for_the_kettle_to_empty": [
        seg(30, phase=9, sat=[], cmd=START), seg(3, sat=[9]), reset(),
    ],
    "commands_that_must_be_ignored": [
        seg(3, cmd=HOLD), seg(3, cmd=RESTART), seg(3, cmd=ABORT), seg(3, cmd=RESET),
        seg(3, phase=0, cmd=START), seg(3, phase=11, cmd=START),
        seg(10, phase=5, cmd=START), seg(3, cmd=START), seg(3, cmd=RESTART), seg(3, cmd=RESET),
        seg(3, cmd=HOLD), seg(3, cmd=START), seg(3, cmd=RESET), seg(3, cmd=HOLD), seg(3, cmd=9),
        seg(3, cmd=ABORT), reset(),
    ],
    "entry_to_the_mash_tun_holds_and_refuses": [
        seg(20, phase=3, sat=[], cmd=START), seg(3, lockM=True),     # held at once
        seg(3, cmd=RESTART),                                          # refused, XA01 latched
        seg(3, lockM=False), seg(5, cmd=RESTART), seg(3, sat=[3]), reset(),
        seg(5, lockM=True, phase=4, cmd=START),                       # refused in Idle
        seg(5, phase=2, cmd=START), seg(2, cmd=ABORT), reset(), seg(3, lockM=False),
    ],
    "entry_to_the_kettle_holds_and_refuses": [
        seg(5, phase=7, lockK=True, cmd=START), seg(3, lockK=False),
        seg(10, phase=9, sat=[], cmd=START), seg(3, lockK=True), seg(3, lockK=False, cmd=RESTART),
        seg(2, cmd=ABORT), reset(),
        seg(3, phase=6, lockM=True, lockK=True, cmd=START), seg(3, lockM=False), seg(3, lockK=False),
        seg(5, phase=6, sat=[], cmd=START), seg(3, lockK=True), seg(3, lockK=False, cmd=RESTART),
        seg(3, sat=[6]), reset(),
    ],
    "entry_holds_a_phase_that_would_complete": [
        seg(5, phase=9, sat=[], cmd=START), seg(3, lockK=True, sat=[9]),
        seg(3, lockK=False, cmd=RESTART), seg(3), reset(),
    ],
}


def expand(segments: list[Segment]) -> list[dict[str, Any]]:
    """One entry per scan: the fields of a segment on its first scan, none on the rest."""
    scans: list[dict[str, Any]] = [{}, {}]
    for item in segments:
        fields = {k: v for k, v in item.items() if k != "n"}
        scans.append(fields)
        scans.extend({} for _ in range(item["n"] - 1))
    return scans


def drive_conditions(plc: st_engine.CompiledPlc, sat: set[int]) -> None:
    """Set the inputs the condition phases read so exactly the phases in ``sat`` are done."""
    plc.set("BH_HLT_TT01_PV", 1700 if 2 in sat else 0)           # at the 168.0 degF setpoint
    plc.set("BH_HLT_LT01_PV", 900 if 2 in sat else 0)
    plc.set("BH_MLT_LT01_PV", 700 if 3 in sat else 0)            # mashed in at 60 %
    plc.set("BH_KTL_LT01_PV", 900 if 6 in sat else (0 if 9 in sat else 500))  # full, empty, or between


def run_st(template: st_engine.CompiledPlc, scans: list[dict[str, Any]]) -> list[list[int]]:
    """The compiled program through the scans: [state, step, XA01, XA02, command] after each."""
    plc = template.fresh()
    plc.set("BH_MLT_TT01_PV", 1520)  # at the mash setpoint unless a scan says otherwise
    drive_conditions(plc, set())
    rows: list[list[int]] = []
    for k, scan in enumerate(scans):
        if "phase" in scan:
            plc.set("BH_BATCH_XC01_PHASE", scan["phase"])
        if "param" in scan:
            plc.set("BH_BATCH_XC03_PARAM", scan["param"])
        if "lockM" in scan:
            plc.set("BH_ENTRY_HS01_ENTRY", scan["lockM"])
        if "lockK" in scan:
            plc.set("BH_ENTRY_HS02_ENTRY", scan["lockK"])
        if "sat" in scan:
            drive_conditions(plc, set(scan["sat"]))
        if "temp" in scan:
            plc.set("BH_MLT_TT01_PV", 1520 if scan["temp"] else 1000)
        if scan.get("cmd"):
            plc.set("BH_BATCH_XC01_CMD", scan["cmd"])
        plc.scan((k + 1) * SCAN_NS)
        rows.append([
            plc.get("BH_BATCH_XS01_STATE"), plc.get("BH_BATCH_XS01_STEP"),
            plc.get("BH_ENTRY_XA01_PV"), plc.get("BH_ENTRY_XA02_PV"), plc.get("BH_BATCH_XC01_CMD"),
        ])
    return rows


def run_port(scans: list[dict[str, Any]]) -> list[list[int]]:
    """The JavaScript port through the same scans."""
    done = subprocess.run(  # noqa: S603
        [require_node(), str(TRACE)],
        input=json.dumps({"scans": scans}),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    rows: list[list[int]] = json.loads(done.stdout)["rows"]
    return rows


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> st_engine.CompiledPlc:
    """plc-brewhouse's ST compiled once (the tests skip without the toolchain)."""
    require_node()
    gap = st_engine.toolchain_gap()
    if gap:
        pytest.skip(f"compiled ST unavailable: {gap}")
    return st_engine.CompiledPlc.build("plc-brewhouse", "full", tmp_path_factory.mktemp("plc-brewhouse"))


@pytest.fixture(scope="module")
def traces(template: st_engine.CompiledPlc) -> Callable[[str], tuple[list[list[int]], list[list[int]]]]:
    """Each scenario's rows from the program and from the port, run once."""
    done: dict[str, tuple[list[list[int]], list[list[int]]]] = {}

    def get(name: str) -> tuple[list[list[int]], list[list[int]]]:
        if name not in done:
            scans = expand(SCENARIOS[name])
            done[name] = (run_st(template, scans), run_port(scans))
        return done[name]

    return get


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_port_matches_the_program_every_scan(
    name: str, traces: Callable[[str], tuple[list[list[int]], list[list[int]]]]
) -> None:
    st_rows, port_rows = traces(name)
    assert len(st_rows) == len(port_rows)
    for k, (want, got) in enumerate(zip(st_rows, port_rows, strict=True)):
        assert got == want, (
            f"{name}: scan {k} [state, step, XA01, XA02, command]: program {want}, port {got}; "
            f"before it: program {st_rows[max(0, k - 3):k]}, port {port_rows[max(0, k - 3):k]}"
        )


def test_the_traces_reach_every_state_and_alarm(
    traces: Callable[[str], tuple[list[list[int]], list[list[int]]]],
) -> None:
    """The comparison is not vacuous: between them the traces visit every state of the
    handshake, both entry alarms, and every completion."""
    seen_states: set[int] = set()
    xa01 = xa02 = False
    for name in SCENARIOS:
        for state, _step, a1, a2, _cmd in traces(name)[0]:
            seen_states.add(state)
            xa01 = xa01 or bool(a1)
            xa02 = xa02 or bool(a2)
    assert seen_states == {IDLE, RUNNING, HOLDING, HELD, RESTARTING, COMPLETE, ABORTED}
    assert xa01 and xa02
    for name in ("mill_runs_the_grain_bill", "vorlauf_hold_and_restart", "whirlpool_by_minutes",
                 "mash_rest_counts_at_temperature", "defaults_run_their_full_time"):
        assert any(row[0] == COMPLETE for row in traces(name)[0]), f"{name} never completes"
