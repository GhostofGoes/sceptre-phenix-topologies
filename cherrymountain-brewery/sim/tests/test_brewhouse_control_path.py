"""A brew day through the real control path, offline (DESIGN.md 12.1, 12.2, 15.6).

The ``start-batch`` event reaches the brewhouse federate; with a PLC driving, it
presses the brewhouse panel's request counter; the plc-brewhouse program (its ST,
compiled by strucpp and g++) carries the counter to the FUXA batch engine (the script of the
generated project, run under Node) over the OPC UA values; the engine accepts it,
sequences the ten ISA-88 phases through the PLC's command registers, and the PLC's
outputs drive the federate. No autopilot runs. This is the Phase 3 exit criterion,
"a full brew-day batch runs unattended", as far as it can be shown without VMs.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import st_engine

from cherrymountain_sim.federate import HelicsConfig

from brewhouse_sil import ROOT, ControlPath, skip_reason

WHY: str | None = skip_reason()
pytestmark = pytest.mark.skipif(WHY is not None, reason=WHY or "")

#: ISA-88 phase numbers of one brew as the engine runs them: MASH_STEP twice (the
#: recipe's saccharification rest and mash-out).
WHOLE_BREW: list[int] = [1, 2, 3, 4, 4, 5, 6, 7, 8, 9, 10]
COMPLETE: int = 3


@pytest.fixture(scope="module")
def plc_template(tmp_path_factory: pytest.TempPathFactory) -> st_engine.CompiledPlc:
    """plc-brewhouse's ST, compiled once; each test cold-starts its own PLC."""
    return st_engine.CompiledPlc.build("plc-brewhouse", "full", tmp_path_factory.mktemp("plc-brewhouse"))


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The scada-fuxa project as the builder makes it now."""
    spec = importlib.util.spec_from_file_location("cm_fuxa_build_project", ROOT / "fuxa" / "build_project.py")
    assert spec is not None and spec.loader is not None
    builder = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = builder
    spec.loader.exec_module(builder)
    path = tmp_path_factory.mktemp("fuxa") / "scada-full.json"
    path.write_text(builder.render(builder.build_project("full", "scada-fuxa")), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def full() -> HelicsConfig:
    cfg = HelicsConfig.load(ROOT / "plant" / "generated" / "full" / "helics.json")
    brewhouse = cfg.federates["brewhouse"].model_copy(deep=True)
    brewhouse.sim = {**brewhouse.sim, "auto_start": None}  # the test, not the 05:00 schedule, asks
    return cfg.model_copy(update={"federates": {**cfg.federates, "brewhouse": brewhouse}})


@pytest.fixture()
def path(full: HelicsConfig, plc_template: st_engine.CompiledPlc, project: Path) -> Iterator[ControlPath]:
    cp = ControlPath(full, plc_template.fresh(), project)
    try:
        yield cp
    finally:
        cp.close()


def test_a_start_batch_event_brews_a_double_brew_day_through_the_plc_and_the_engine(
    path: ControlPath,
) -> None:
    """What the baseline-brew-day run posts: ``start-batch`` with its default of two
    brews. Two brews, sequenced one after the other by the engine, into one fermenter."""
    cp = path
    cp.run(40)                                    # the PLC writes, the engine initialises
    assert not cp.bh.autopilot, "a PLC is driving"
    assert cp.tag("BATCH.STATE") == 0
    cp.event("start-batch", recipe="fire-of-the-phenix", batch_id="T-0001")  # brews: 2
    assert cp.bh.pilot.queue == 0 and cp.bh.panel_pending == 2

    cellar = cp.lf.feds["cellar"]
    fv = cellar.cellar.fermenters["FV101"]  # type: ignore[attr-defined]
    seen: list[int] = []        # the phase of each Start the PLC took (state left Idle)
    first_wort: dict[str, float] = {}
    state = 0
    for _ in range(14 * 3600):
        now = int(cp.plc.get("BH_BATCH_XS01_STATE"))
        if state == 0 and now in (1, 5):
            seen.append(int(cp.plc.get("ACTIVE")))
        state = now
        if cp.bh.proc.brews_completed == 1 and not first_wort:
            first_wort = {"liters": fv.liquid_l, "plato": fv.gravity_plato, "t": cp.t}
        if cp.tag("BATCH.STATE") == COMPLETE and cp.bh.proc.brews_completed == 2:
            break
        cp.second()
    else:
        pytest.fail(f"no brew day after 14 h: {cp.tag('BATCH.STATUS')}; phases {seen}")

    # the ten phases of each brew, in order, with MASH_STEP once per recipe step
    assert seen == WHOLE_BREW + WHOLE_BREW
    # the request was accepted (both brews) and the plant knows
    assert cp.bh.panel_requests == 2 and cp.bh.panel_accepted == 2 and cp.bh.panel_pending == 0
    assert cp.tag("BATCH.NUMBER") == 2 and cp.tag("BATCH.COMPLETED") == 2
    assert cp.tag("BATCH.ID") == "FOP-0002"
    # no autopilot drove any of it
    assert not cp.bh.autopilot and cp.bh.pilot.phase == 0 and cp.bh.pilot.queue == 0
    # the first brew really brewed: the grain was milled to the recipe (about 16 degP,
    # not the 5 a short mill gives), and the wort reached the fermenter the engine reserved
    assert cp.tag("BATCH.FV") == 1 and cp.tag("BATCH.FV_BREWS") == 2
    assert fv.filled and first_wort["liters"] > 1500.0
    assert 12.0 < first_wort["plato"] < 20.0
    # the second brew topped the same fermenter up (a 30 bbl FV takes two 15 bbl brews)
    assert fv.liquid_l == pytest.approx(fv.cfg.volume_l, rel=0.001)
    # the crew's tasks happened (the control system leaves them to people): hops went
    # in, the spent grain and the trub are out, so the second brew started clean
    assert cp.max_ibu > 40.0, "the boil and whirlpool hops went in"
    assert cp.bh.proc.grain_kg == 0.0 and cp.bh.proc.mlt.liters == 0.0 and cp.bh.proc.kettle.liters == 0.0
    assert cp.bh.proc.silo_kg < 18000.0 - 2 * 480.0, "the silo gave up two grain bills"
    # every alarm of the engine is clear, and the PLC is back to Idle
    assert [cp.tag(f"BATCH.ALM_{k}") for k in ("TIMEOUT", "PLC", "REFUSED", "NOFV", "ENGINE")] == [0] * 5
    assert int(cp.plc.get("BH_BATCH_XS01_STATE")) == 0
    # a double-brew day takes about 11 h (DESIGN.md 3.1), the second brew's mash-in about
    # 3.5 to 4 h after the first's
    assert 10 * 3600 < cp.t < 13 * 3600


def test_a_request_made_while_fuxa_is_down_waits_at_the_panel(
    path: ControlPath, caplog: pytest.LogCaptureFixture
) -> None:
    """PLC present, FUXA down: the event is counted at the panel and warned about,
    not lost; the engine takes it when it is back."""
    cp = path
    cp.run(40)
    cp.engine_up = False
    caplog.set_level(logging.WARNING, logger="cherrymountain_sim.brewhouse")
    cp.event("start-batch", brews=2)
    cp.run(10 * 60)
    assert cp.bh.panel_pending == 2 and cp.bh.panel_requests == 2
    assert int(cp.plc.get("BH_BATCH_XI02_REQUESTS")) == 2, "the PLC sees the counter"
    assert [r for r in caplog.records if "not accepted" in r.getMessage()], "and it is not silent"
    assert int(cp.plc.get("ACTIVE")) == 0 and not cp.bh.on("mill.run"), "nothing brews"
    cp.engine_up = True
    cp.until(lambda: cp.bh.panel_pending == 0 and cp.bh.on("mill.run"), 300, "the engine to take it and mill")
    assert cp.tag("BATCH.QUEUE") == 1 and cp.tag("BATCH.NUMBER") == 1
    assert int(cp.plc.get("BH_BATCH_XC02_ACCEPTED")) == 2
    assert int(cp.plc.get("BH_BATCH_XI03_ACCEPTED")) == 2


def test_the_brew_day_schedule_starts_the_engine_without_an_event(
    full: HelicsConfig, plc_template: st_engine.CompiledPlc, project: Path
) -> None:
    """05:00 on a brew day presses the panel; the engine starts the brew on its own."""
    cfg = full.model_copy(deep=True)
    cfg.federates["brewhouse"].sim["auto_start"] = "05:00"
    cp = ControlPath(cfg, plc_template.fresh(), project)
    try:
        cp.run(31 * 60)                            # the clock starts at 04:30
        assert cp.bh.now.hour == 5 and not cp.bh.autopilot
        assert cp.bh.panel_requests == 2
        cp.until(lambda: cp.bh.on("mill.run"), 300, "the first mill run")
        assert cp.tag("BATCH.STATE") == 1 and cp.tag("BATCH.QUEUE") == 1
    finally:
        cp.close()
