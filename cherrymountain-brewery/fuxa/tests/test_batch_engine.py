"""The batch engine script of the generated scada-fuxa project, run under Node.

The script is the one in the project JSON that is POSTed to FUXA, compiled the way
FUXA compiles a server script and run against a stub of FUXA's script API
(fuxa_stub.mjs, behavior taken from the FUXA source at the commit the cm-fuxa image
pins) and a simulator of plc-brewhouse's handshake, plc-cellar's fermenter status,
and the panel's request counter (brewhouse_sim.mjs). Each scenario of
batch_scenarios.mjs is one test: a whole unattended brew through all ten phases, a
double-brew day, Hold and Restart, Abort, a phase that never completes, no fermenter
ready, a start refused (PLC not idle, vessel in entry), a lost PLC link, a PLC or FUXA
restart, a request that arrives while FUXA is down, and a script that fails.
"""

from __future__ import annotations

import atexit
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from fuxa_test_support import FUXA_DIR, NODE, build_project, require_node

HARNESS: Path = FUXA_DIR / "tests" / "batch_scenarios.mjs"
#: The lite variant has the same brewhouse chain; a smoke test of it is enough.
LITE_SCENARIOS: tuple[str, ...] = ("full_unattended_sequence", "operator_start_runs_a_brew")

_projects: Path = Path(tempfile.mkdtemp(prefix="cm-batch-engine-"))
atexit.register(shutil.rmtree, _projects, True)


def project_file(variant: str) -> Path:
    path: Path = _projects / f"scada-{variant}.json"
    if not path.exists():
        path.write_text(json.dumps(build_project(variant)), encoding="utf-8")
    return path


def run_node(variant: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [require_node(), str(HARNESS), "--project", str(project_file(variant)), *args],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


def scenario_names() -> list[str]:
    """The scenarios of the harness (asked of Node, so the list cannot drift)."""
    if NODE is None:
        return ["needs_node"]
    done = run_node("full", "--list")
    assert done.returncode == 0, done.stderr
    return done.stdout.split()


SCENARIOS: list[str] = scenario_names()


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_scenario_full(scenario: str) -> None:
    done = run_node("full", scenario)
    assert done.returncode == 0, f"{done.stdout}\n{done.stderr}"
    assert done.stdout.startswith(f"PASS {scenario}")


@pytest.mark.parametrize("scenario", LITE_SCENARIOS)
def test_scenario_lite(scenario: str) -> None:
    done = run_node("lite", scenario)
    assert done.returncode == 0, f"{done.stdout}\n{done.stderr}"


def test_the_harness_covers_what_the_task_names() -> None:
    """The scenarios the exit criterion and the reviewers asked for exist."""
    required = {
        "full_unattended_sequence",
        "hold_and_restart",
        "abort_then_reset",
        "phase_timeout_raises_the_alarm",
        "no_fermenter_waits_then_proceeds",
        "start_refused_when_not_idle",
        "entry_lockout_refuses_and_holds",
    }
    if NODE is None:
        pytest.skip("node not found")
    assert required <= set(SCENARIOS)
