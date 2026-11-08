"""plantgen: addressing, consistency checks, and --check mode."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from plantgen import checks
from plantgen.allocate import IecAddress, allocate, iec_to_slave
from plantgen.cli import DEFAULT_PLANT, generate, main
from plantgen.model import Plant, PlantError, expand, load, load_file
from plantgen.render import render_all

COMMITTED: Path = DEFAULT_PLANT.parent / "generated" / "ci"


@pytest.fixture(scope="module")
def plant() -> Plant:
    """The ci variant."""
    return load(DEFAULT_PLANT, "ci")


def write_plant(tmp_path: Path, mutate: Callable[[dict[str, Any]], None]) -> Path:
    """Copy plant.yaml with ``mutate(data)`` applied; return the new path."""
    data = yaml.safe_load(DEFAULT_PLANT.read_text(encoding="utf-8"))
    mutate(data)
    out = tmp_path / "plant.yaml"
    out.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return out


def test_generate_ci_is_clean() -> None:
    """The committed plant database passes every consistency check."""
    files = generate(DEFAULT_PLANT, "ci")
    assert "phenix/ot-sim.yaml" in files
    assert "openplc/plc-cellar/modbus_master.json" in files
    assert "fuxa/scada-fuxa-tags.json" in files


def test_fv101_tt01_pv_in_every_convention(plant: Plant) -> None:
    """The e2e tag: ot-sim 30002, %IW101, slave input 101, FUXA 300000/102."""
    a = allocate(plant).tags["CEL.FV101.TT01.PV"]
    assert (a.rio_space, a.rio_address, a.master_fc) == ("input", 30002, 4)
    assert str(a.plc_iec) == "%IW101"
    assert (a.slave_space, a.slave_address) == ("input", 101)
    assert (a.fuxa_memaddress, a.fuxa_address) == ("300000", 102)
    files = render_all(plant, allocate(plant))
    fuxa = json.loads(files["fuxa/scada-fuxa-tags.json"])
    tag = next(
        t for t in fuxa["devices"][0]["tags"] if t["name"] == "CEL.FV101.TT01.PV"
    )
    assert tag["divisor"] == 100 and tag["type"] == "UInt16"


def test_otsim_bases_and_contiguity(plant: Plant) -> None:
    """The phenix app's literal bases: 1, 10001, 30001, 40001."""
    regs = allocate(plant).rio_registers["rio-cellar"]
    firsts = {
        sp: min(r.address for r in regs if r.space == sp)
        for sp in {r.space for r in regs}
    }
    assert firsts == {"coil": 1, "discrete": 10001, "input": 30001, "holding": 40001}
    assert len(regs) == 1 + 4 * 8  # clock + 4 FVs x 8 points


def test_modbus_master_offsets_equal_otsim_literals(plant: Plant) -> None:
    """modbus_master uses the literal numbers as PDU offsets, not 0-based."""
    files = render_all(plant, allocate(plant))
    cfg = json.loads(files["openplc/plc-cellar/modbus_master.json"])
    points = {p["fc"]: p for p in cfg[0]["config"]["io_points"]}
    assert points[4]["offset"] == "30001" and points[4]["iec_location"] == "%IW100"
    assert points[2]["offset"] == "10001" and points[2]["iec_location"] == "%IX100.0"
    assert points[15]["offset"] == "1" and points[15]["iec_location"] == "%QX100.0"
    assert points[16]["offset"] == "40001" and points[16]["iec_location"] == "%QW100"
    assert cfg[0]["config"]["host"] == "10.20.12.10"


def test_slave_map_matches_openplc() -> None:
    """%QX/%IX bits from 0; %IW/%QW words; %MW is holding 1024+."""
    assert iec_to_slave(IecAddress.parse("%QX100.0")) == ("coil", 800)
    assert iec_to_slave(IecAddress.parse("%IX0.7")) == ("discrete", 7)
    assert iec_to_slave(IecAddress.parse("%IW100")) == ("input", 100)
    assert iec_to_slave(IecAddress.parse("%QW5")) == ("holding", 5)
    assert iec_to_slave(IecAddress.parse("%MW0")) == ("holding", 1024)
    assert str(IecAddress.parse("%QX1.3")) == "%QX1.3"


def test_every_alarm_rationalized_and_bit_allocated(plant: Plant) -> None:
    """Each alarm has its PLC bit, FUXA tag, and rationalization."""
    alloc = allocate(plant)
    assert plant.alarms
    for a in plant.alarms:
        assert a.name in alloc.tags and alloc.tags[a.name].slave_space == "coil"
        assert a.cause and a.consequence and a.action and a.response_time_s > 0
    assert checks.alarms_rationalized(plant, alloc) == []


def test_ntp_lists_every_host(plant: Plant) -> None:
    """ci ntp app lists all five hosts, with reachable sources."""
    files = render_all(plant, allocate(plant))
    ntp = yaml.safe_load(files["phenix/ntp.yaml"])
    assert [h["hostname"] for h in ntp["hosts"]] == plant.spec.hosts
    assert ntp["metadata"]["defaultSource"] == {
        "hostname": "sim-ctl",
        "interface": "mgmt",
    }


def test_helics_lists_are_closed(plant: Plant) -> None:
    """Every rio-cellar subscription is published by the cellar federate."""
    files = render_all(plant, allocate(plant))
    doc = json.loads(files["helics.json"])
    assert doc["federate_count"] == 2
    assert doc["federates"]["cellar"]["subscriptions"] == []  # no peers in ci
    assert checks.helics_closed(plant, files) == []


# -- failure detection -------------------------------------------------------


def test_detects_off_by_one_in_modbus_master(plant: Plant) -> None:
    """A 0-based (30000) offset is caught register by register."""
    alloc = allocate(plant)
    files = render_all(plant, alloc)
    key = "openplc/plc-cellar/modbus_master.json"
    files[key] = files[key].replace('"offset": "30001"', '"offset": "30000"')
    errs = checks.master_agrees(plant, alloc, files)
    assert any("unconfigured register" in e for e in errs)


def test_detects_fuxa_off_by_one(plant: Plant) -> None:
    """A FUXA address that forgot the +1 is caught."""
    alloc = allocate(plant)
    files = render_all(plant, alloc)
    key = "fuxa/scada-fuxa-tags.json"
    doc = json.loads(files[key])
    doc["devices"][0]["tags"][0]["address"] -= 1
    files[key] = json.dumps(doc)
    assert checks.fuxa_agrees(plant, alloc, files)


def test_detects_register_gap(plant: Plant) -> None:
    """Moving one RIO register leaves a gap."""
    alloc = allocate(plant)
    alloc.rio_registers["rio-cellar"][-1].address += 5
    assert checks.no_gaps(plant, alloc)


def test_detects_reordered_app_block(plant: Plant) -> None:
    """If the app block's device order differs from the map, it is caught."""
    alloc = allocate(plant)
    files = render_all(plant, alloc)
    app = yaml.safe_load(files["phenix/ot-sim.yaml"])
    devs = app["hosts"][0]["metadata"]["modbus"]["devices"]
    devs[1], devs[2] = devs[2], devs[1]
    files["phenix/ot-sim.yaml"] = yaml.safe_dump(app)
    assert checks.otsim_agrees(plant, alloc, files)


def test_detects_unrationalized_alarm(tmp_path: Path) -> None:
    """An alarm without a cause or response time fails the checks."""

    def mutate(d: dict[str, Any]) -> None:
        tag = d["areas"][0]["units"][1]["tags"][0]
        tag["alarms"][0] = {"kind": "deviation", "setpoint": "TT01.SP", "limit": 2.0}

    with pytest.raises(PlantError, match="rationalization"):
        generate(write_plant(tmp_path, mutate), "ci")


def test_detects_unbound_point(tmp_path: Path) -> None:
    """A device point with no tag would be an unnamed register."""

    def mutate(d: dict[str, Any]) -> None:
        tags = d["areas"][0]["units"][1]["tags"]
        tags[:] = [t for t in tags if t["id"] != "ZSO01.PV"]

    with pytest.raises(PlantError, match="bound to 0 tags"):
        generate(write_plant(tmp_path, mutate), "ci")


def test_detects_missing_owner(tmp_path: Path) -> None:
    """A tag whose PLC is not in the variant has no owner."""

    def mutate(d: dict[str, Any]) -> None:
        d["variants"]["ci"]["hosts"].remove("plc-cellar")
        del d["variants"]["ci"]["ntp"]["hosts"]["plc-cellar"]

    with pytest.raises(PlantError, match="owner PLC"):
        generate(write_plant(tmp_path, mutate), "ci")


def test_schema_rejects_unknown_keys_and_bad_ids(tmp_path: Path) -> None:
    """Pydantic validation catches typos in plant.yaml."""

    def typo(d: dict[str, Any]) -> None:
        d["areas"][0]["units"][1]["tags"][0]["unts"] = "degF"

    with pytest.raises(PlantError, match="unts"):
        load_file(write_plant(tmp_path, typo))

    def bad_id(d: dict[str, Any]) -> None:
        d["areas"][0]["units"][1]["tags"][0]["id"] = "TT1.PV"

    with pytest.raises(PlantError, match="TT01.PV"):
        load_file(write_plant(tmp_path, bad_id))
    with pytest.raises(PlantError, match="unknown variant"):
        expand(load_file(DEFAULT_PLANT), "nope")


def test_range_must_fit_uint16(tmp_path: Path) -> None:
    """hi x 10^scaling above 65535 is rejected."""

    def mutate(d: dict[str, Any]) -> None:
        d["areas"][0]["units"][1]["tags"][0]["range"] = [20, 900]

    with pytest.raises(PlantError, match="uint16"):
        generate(write_plant(tmp_path, mutate), "ci")


# -- --check mode -------------------------------------------------------------


def test_check_passes_on_committed_outputs() -> None:
    """The committed generated/ci tree is current."""
    assert main(["--check"]) == 0


def test_check_fails_on_stale_or_extra_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--check reports stale, missing, and unexpected files."""
    out = tmp_path / "ci"
    shutil.copytree(COMMITTED, out)
    assert main(["--check", "--out", str(out)]) == 0
    (out / "registers.csv").write_text("stale\n", encoding="utf-8")
    (out / "extra.json").write_text("{}\n", encoding="utf-8")
    (out / "alarms.json").unlink()
    assert main(["--check", "--out", str(out)]) == 1
    err = capsys.readouterr().err
    assert "stale: registers.csv" in err
    assert "unexpected: extra.json" in err
    assert "missing: alarms.json" in err


def test_generate_writes_and_prunes(tmp_path: Path) -> None:
    """Generation writes every file and removes stale ones."""
    out = tmp_path / "ci"
    (out / "old").mkdir(parents=True)
    (out / "old" / "gone.txt").write_text("x", encoding="utf-8")
    assert main(["--out", str(out)]) == 0
    assert not (out / "old").exists()
    assert main(["--check", "--out", str(out)]) == 0
