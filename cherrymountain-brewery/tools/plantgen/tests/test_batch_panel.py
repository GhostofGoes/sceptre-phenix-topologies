"""plantgen: the brewhouse panel's batch-request points and the phase parameter."""

from __future__ import annotations

import json
from typing import Any

import pytest
import yaml

from plantgen.allocate import allocate
from plantgen.cli import DEFAULT_PLANT
from plantgen.model import Plant, load
from plantgen.render import render_all

PANEL_TAGS: tuple[str, ...] = (
    "BH.BATCH.XI02.REQUESTS",
    "BH.BATCH.XC02.ACCEPTED",
    "BH.BATCH.XI03.ACCEPTED",
    "BH.BATCH.XC03.PARAM",
)


@pytest.fixture(scope="module", params=["full", "lite"])
def plant(request: pytest.FixtureRequest) -> Plant:
    return load(DEFAULT_PLANT, request.param)


@pytest.fixture(scope="module")
def files(plant: Plant) -> dict[str, str]:
    return render_all(plant, allocate(plant))


def test_the_panel_registers_are_appended_to_the_rio(plant: Plant) -> None:
    """The panel is the last device of rio-brewhouse, so no earlier register moves:
    its request counter is the next input register after the knockout's, and the
    accepted counter the next holding register."""
    a = allocate(plant)
    req, acc = a.tags["BH.BATCH.XI02.REQUESTS"], a.tags["BH.BATCH.XI03.ACCEPTED"]
    assert (req.rio_space, req.rio_address, req.master_fc, str(req.plc_iec)) == ("input", 30021, 4, "%IW120")
    assert (acc.rio_space, acc.rio_address, acc.master_fc, str(acc.plc_iec)) == ("holding", 40011, 16, "%QW110")
    # the knockout's last points keep the numbers they had
    assert a.tags["BH.KO.AT01.PV"].rio_address == 30020
    assert a.tags["BH.KO.FC01.SP"].rio_address == 40010
    regs = a.rio_registers["rio-brewhouse"]
    for space in ("input", "holding"):
        addresses = sorted(r.address for r in regs if r.space == space)
        assert addresses == list(range(addresses[0], addresses[0] + len(addresses))), f"{space} has no gap"


def test_the_plc_reads_and_writes_the_new_registers(files: dict[str, str]) -> None:
    master = json.loads(files["openplc/plc-brewhouse/modbus_master.json"])
    blocks = {(b["fc"], b["iec_location"]): b for b in master[0]["config"]["io_points"]}
    assert blocks[(4, "%IW100")]["len"] == 21 and blocks[(16, "%QW100")]["len"] == 11
    located = files["openplc/plc-brewhouse/located_vars.st"]
    assert "BH_BATCH_XI02_REQUESTS AT %IW120 : UINT" in located
    assert "BH_BATCH_XI03_ACCEPTED AT %QW110 : UINT" in located
    # the engine's two PLC-memory words (written over OPC UA, never by the program)
    assert "BH_BATCH_XC02_ACCEPTED AT %MW" in located and "BH_BATCH_XC03_PARAM AT %MW" in located


def test_otsim_serves_the_panel_device(files: dict[str, str]) -> None:
    app: dict[str, Any] = yaml.safe_load(files["phenix/ot-sim.yaml"])
    rio = next(h for h in app["hosts"] if h["hostname"] == "rio-brewhouse")
    devices = rio["metadata"]["modbus"]["devices"]
    assert devices[-1] == {"name": "brewhouse/panel", "type": "batch-panel", "endpoint": "updates"}
    types = app["metadata"]["infrastructures"]["brewery"]["batch-panel"]
    assert types["requests"]["type"] == "analog-read" and types["accepted"]["type"] == "analog-read-write"
    assert types["requests"]["modbus"] == {"data-type": "uint16", "scaling": 0}


def test_fuxa_reads_and_writes_what_the_engine_needs(files: dict[str, str]) -> None:
    fuxa = json.loads(files["fuxa/scada-fuxa-tags.json"])
    bh = {t["name"]: t for d in fuxa["devices"] if d["name"] == "plc-brewhouse" for t in d["tags"]}
    assert bh["BH.BATCH.XI02.REQUESTS"]["access"] == "read"
    assert bh["BH.BATCH.XI03.ACCEPTED"]["access"] == "read"
    # the engine writes the accepted count and the phase parameter, over OPC UA
    assert bh["BH.BATCH.XC02.ACCEPTED"]["access"] == "read-write"
    assert bh["BH.BATCH.XC03.PARAM"]["access"] == "read-write"
    # the engine needs these PLC tags to be writable too
    for name in ("BH.BATCH.XC01.PHASE", "BH.BATCH.XC01.CMD", "BH.BATCH.XI01.BATCH",
                 "BH.HLT.TT01.SP", "BH.MLT.TT01.SP", "BH.KO.TT02.SP"):
        assert bh[name]["access"] == "read-write", name
    assert bh["BH.BATCH.XS01.STATE"]["access"] == "read"


def test_the_brewhouse_federate_publishes_the_counter_and_takes_the_echo(files: dict[str, str]) -> None:
    cfg = json.loads(files["helics.json"])["federates"]["brewhouse"]
    pubs = {p["key"]: p for p in cfg["publications"]}
    assert pubs["brewhouse/panel.requests"]["device_type"] == "batch-panel"
    assert "panel.accepted" in cfg["accepted_updates"] and "panel.requests" not in cfg["accepted_updates"]


def test_the_ci_variant_is_untouched() -> None:
    ci = load(DEFAULT_PLANT, "ci")
    assert not [t for t in ci.tags if t.area == "BH"]
