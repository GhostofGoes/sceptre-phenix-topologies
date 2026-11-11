"""Fixtures shared by the PLC tests: the compiled ST of plc-power, or its model."""

from __future__ import annotations

from collections.abc import Callable

import plc_power_model
import pytest
import st_engine

from cherrymountain_sim.plcloop import PlcEngine


@pytest.fixture(scope="session")
def st_template(tmp_path_factory: pytest.TempPathFactory) -> st_engine.CompiledPlc | None:
    """plc-power's ST compiled by strucpp and g++, once (``None`` without the toolchain)."""
    if st_engine.toolchain_gap():
        return None
    return st_engine.CompiledPlc.build(
        "plc-power", "full", tmp_path_factory.mktemp("plc-power")
    )


@pytest.fixture(params=["model", "st"])
def make_plc(
    request: pytest.FixtureRequest, st_template: st_engine.CompiledPlc | None
) -> Callable[[], PlcEngine]:
    """A factory of cold-started plc-power PLCs: the Python model, or the compiled ST
    (which skips where Node, a built strucpp checkout, or g++ is missing)."""
    if request.param == "model":
        return plc_power_model.PlcPowerModel
    if st_template is None:
        pytest.skip(f"compiled ST unavailable: {st_engine.toolchain_gap()}")
    return st_template.fresh


@pytest.fixture
def st_plc(st_template: st_engine.CompiledPlc | None) -> st_engine.CompiledPlc:
    """A cold-started compiled plc-power (skips without the toolchain)."""
    if st_template is None:
        pytest.skip(f"compiled ST unavailable: {st_engine.toolchain_gap()}")
    return st_template.fresh()
