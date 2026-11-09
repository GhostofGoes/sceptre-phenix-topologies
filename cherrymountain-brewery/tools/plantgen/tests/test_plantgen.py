"""plantgen: addressing, consistency checks, and --check mode."""

from __future__ import annotations

import itertools
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from plantgen import checks
from plantgen.allocate import IecAddress, SlaveLayout, allocate
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


def unit(data: dict[str, Any], area: str, unit_id: str) -> dict[str, Any]:
    """The plant.yaml unit ``unit_id`` of ``area`` (raw data, for mutation)."""
    for a in data["areas"]:
        if a["id"] == area:
            return next(u for u in a["units"] if u["id"] == unit_id)
    raise KeyError(area)


def test_generate_ci_is_clean() -> None:
    """The committed plant database passes every consistency check."""
    files = generate(DEFAULT_PLANT, "ci")
    assert "phenix/ot-sim.yaml" in files
    assert "openplc/plc-cellar/modbus_master.json" in files
    assert "openplc/plc-cellar/modbus_slave.json" in files
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


#: The ci cellar PLC's layout: %QW0-103 (RIO outputs at %QW100), %MW0-7, %IW0-116,
#: %QX0.0-%QX100.7 (808 bits), %IX0.0-%IX100.3 (808 bits after byte rounding).
CELLAR_LAYOUT: SlaveLayout = SlaveLayout(
    qw_count=104, mw_count=8, qx_bits=808, ix_bits=808, iw_count=117
)


def test_slave_layout_is_tight_and_v4_segmented(plant: Plant) -> None:
    """%MW0 follows the %QW segment (v4.3.2), so it is holding 104, not 1024."""
    alloc = allocate(plant)
    layout = alloc.layouts["plc-cellar"]
    assert layout == CELLAR_LAYOUT
    assert layout.mw_start == 104
    sp = alloc.tags["CEL.FV101.TT01.SP"]
    assert (str(sp.plc_iec), sp.slave_space, sp.slave_address) == (
        "%MW0",
        "holding",
        104,
    )
    assert sp.fuxa_address == 105
    last = alloc.tags["CEL.FV104.TT02.SP"]
    assert (str(last.plc_iec), last.slave_address) == ("%MW7", 111)


def test_slave_map_matches_openplc_v4() -> None:
    """%QX/%IX bits from 0; %IW words; holding is %QW then %MW then %MD then %ML."""
    layout = CELLAR_LAYOUT
    assert layout.to_slave(IecAddress.parse("%QX100.0")) == ("coil", 800)
    assert layout.to_slave(IecAddress.parse("%IX0.7")) == ("discrete", 7)
    assert layout.to_slave(IecAddress.parse("%IW100")) == ("input", 100)
    assert layout.to_slave(IecAddress.parse("%QW5")) == ("holding", 5)
    assert layout.to_slave(IecAddress.parse("%MW0")) == ("holding", 104)
    assert layout.to_slave(IecAddress.parse("%MW7")) == ("holding", 111)
    with pytest.raises(ValueError, match="not exposed"):
        layout.to_slave(IecAddress.parse("%MW8"))  # beyond mw_count: the runtime clamps
    with pytest.raises(ValueError, match="not exposed"):
        layout.to_slave(IecAddress.parse("%QW104"))
    assert str(IecAddress.parse("%QX1.3")) == "%QX1.3"


def test_layout_counts_and_mw_start_follow_the_program() -> None:
    """Counts are highest index + 1 (bits round up to bytes); MW0 sits after %QW."""
    iecs = [
        IecAddress.parse(x) for x in ("%QW0", "%QW2", "%MW0", "%MW3", "%IW9", "%QX0.0")
    ]
    layout = SlaveLayout.from_addresses(iecs)
    assert (layout.qw_count, layout.mw_count, layout.iw_count) == (3, 4, 10)
    assert (layout.qx_bits, layout.ix_bits) == (8, 0)
    assert layout.mw_start == 3
    bigger = SlaveLayout.from_addresses([*iecs, IecAddress.parse("%QW9")])
    assert bigger.mw_start == 10  # a new %QW pushes every %MW up one register
    assert bigger.to_slave(IecAddress.parse("%MW0")) == ("holding", 10)


def test_reference_resolver_inverts_every_tag(plant: Plant) -> None:
    """Replaying the runtime's segment walk on the rendered JSON finds each tag."""
    alloc = allocate(plant)
    files = render_all(plant, alloc)
    mapping = json.loads(files["openplc/plc-cellar/modbus_slave.json"])[
        "buffer_mapping"
    ]
    assert mapping["holding_registers"] == {
        "qw_count": 104,
        "mw_count": 8,
        "md_count": 0,
        "ml_count": 0,
    }
    for a in alloc.tags.values():
        assert checks.resolve_slave(mapping, a.slave_space, a.slave_address) == str(
            a.plc_iec
        )
    # one past the last segment is outside the map
    assert checks.resolve_slave(mapping, "holding", 112) is None
    assert checks.resolve_slave(mapping, "holding", 1024) is None
    assert checks.resolve_slave(mapping, "coil", 808) is None
    assert checks.resolve_slave(mapping, "input", 117) is None


def test_resolver_handles_md_ml_and_mx_segments() -> None:
    """%MD takes two registers and %ML four, after %MW; %MX follows %QX."""
    mapping = {
        "holding_registers": {
            "qw_count": 2,
            "mw_count": 3,
            "md_count": 2,
            "ml_count": 1,
        },
        "coils": {"qx_bits": 16, "mx_bits": 8},
        "discrete_inputs": {"ix_bits": 8},
        "input_registers": {"iw_count": 4},
    }
    got = [checks.resolve_slave(mapping, "holding", n) for n in range(11)]
    assert got == [
        "%QW0",
        "%QW1",
        "%MW0",
        "%MW1",
        "%MW2",
        "%MD0",
        "%MD0",
        "%MD1",
        "%MD1",
        "%ML0",
        "%ML0",
    ]
    assert checks.resolve_slave(mapping, "holding", 12) == "%ML0"
    assert checks.resolve_slave(mapping, "holding", 13) is None
    assert checks.resolve_slave(mapping, "coil", 15) == "%QX1.7"
    assert checks.resolve_slave(mapping, "coil", 16) == "%MX0.0"


def test_modbus_slave_json_has_explicit_tight_buffer_mapping(plant: Plant) -> None:
    """modbus_slave.json pins every segment so the runtime clamps nothing."""
    files = render_all(plant, allocate(plant))
    cfg = json.loads(files["openplc/plc-cellar/modbus_slave.json"])
    assert cfg["network_configuration"] == {"host": "0.0.0.0", "port": 502}
    assert cfg["buffer_mapping"] == CELLAR_LAYOUT.buffer_mapping()
    assert cfg["buffer_mapping"]["coils"] == {"qx_bits": 808, "mx_bits": 0}


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


def test_phenix_yaml_is_block_style(plant: Plant) -> None:
    """The generated phenix app YAML uses flow syntax only for empty collections."""
    files = render_all(plant, allocate(plant))
    names = [name for name in files if name.startswith("phenix/")]
    assert names
    for name in names:
        events = list(yaml.parse(files[name]))
        for event, following in itertools.pairwise(events):
            if isinstance(event, yaml.CollectionStartEvent) and event.flow_style:
                assert isinstance(following, yaml.CollectionEndEvent), name


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


def test_detects_v3_segment_math(plant: Plant) -> None:
    """FUXA addresses computed with the v3 map (%MW0 = holding 1024) are caught."""
    alloc = allocate(plant)
    files = render_all(plant, alloc)
    key = "fuxa/scada-fuxa-tags.json"
    doc = json.loads(files[key])
    sp = next(t for t in doc["devices"][0]["tags"] if t["name"] == "CEL.FV101.TT01.SP")
    assert sp["address"] == 105
    sp["address"] = 1025
    files[key] = json.dumps(doc)
    errs = checks.fuxa_agrees(plant, alloc, files)
    assert any("CEL.FV101.TT01.SP" in e and "None" in e for e in errs)


def test_detects_shifted_mw_segment(plant: Plant) -> None:
    """Growing qw_count in modbus_slave.json moves %MW0, so the FUXA map is wrong."""
    alloc = allocate(plant)
    files = render_all(plant, alloc)
    key = "openplc/plc-cellar/modbus_slave.json"
    cfg = json.loads(files[key])
    cfg["buffer_mapping"]["holding_registers"]["qw_count"] = 1024
    files[key] = json.dumps(cfg)
    assert checks.slave_agrees(plant, alloc, files)  # not the tight layout
    assert any(
        "CEL.FV101.TT01.SP" in e for e in checks.fuxa_agrees(plant, alloc, files)
    )


def test_detects_untight_buffer_mapping(plant: Plant) -> None:
    """A count above the located variables would be clamped by the runtime."""
    alloc = allocate(plant)
    files = render_all(plant, alloc)
    key = "openplc/plc-cellar/modbus_slave.json"
    cfg = json.loads(files[key])
    cfg["buffer_mapping"]["holding_registers"]["mw_count"] = 1024
    files[key] = json.dumps(cfg)
    assert any("tight" in e for e in checks.slave_agrees(plant, alloc, files))


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
        tag = unit(d, "CEL", "FV101")["tags"][0]
        tag["alarms"][0] = {"kind": "deviation", "setpoint": "TT01.SP", "limit": 2.0}

    with pytest.raises(PlantError, match="rationalization"):
        generate(write_plant(tmp_path, mutate), "ci")


def test_detects_unbound_point(tmp_path: Path) -> None:
    """A device point with no tag would be an unnamed register."""

    def mutate(d: dict[str, Any]) -> None:
        tags = unit(d, "CEL", "FV101")["tags"]
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
        unit(d, "CEL", "FV101")["tags"][0]["unts"] = "degF"

    with pytest.raises(PlantError, match="unts"):
        load_file(write_plant(tmp_path, typo))

    def bad_id(d: dict[str, Any]) -> None:
        unit(d, "CEL", "FV101")["tags"][0]["id"] = "TT1.PV"

    with pytest.raises(PlantError, match="TT01.PV"):
        load_file(write_plant(tmp_path, bad_id))
    with pytest.raises(PlantError, match="unknown variant"):
        expand(load_file(DEFAULT_PLANT), "nope")


def test_range_must_fit_uint16(tmp_path: Path) -> None:
    """hi x 10^scaling above 65535 is rejected."""

    def mutate(d: dict[str, Any]) -> None:
        unit(d, "CEL", "FV101")["tags"][0]["range"] = [20, 900]

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
    assert main(["--check", "--variant", "ci", "--out", str(out)]) == 0
    (out / "registers.csv").write_text("stale\n", encoding="utf-8")
    (out / "extra.json").write_text("{}\n", encoding="utf-8")
    (out / "alarms.json").unlink()
    assert main(["--check", "--variant", "ci", "--out", str(out)]) == 1
    err = capsys.readouterr().err
    assert "stale: registers.csv" in err
    assert "unexpected: extra.json" in err
    assert "missing: alarms.json" in err


def test_generate_writes_and_prunes(tmp_path: Path) -> None:
    """Generation writes every file and removes stale ones."""
    out = tmp_path / "ci"
    (out / "old").mkdir(parents=True)
    (out / "old" / "gone.txt").write_text("x", encoding="utf-8")
    assert main(["--variant", "ci", "--out", str(out)]) == 0
    assert not (out / "old").exists()
    assert main(["--check", "--variant", "ci", "--out", str(out)]) == 0


def test_alarm_deadbands_and_off_delays(tmp_path: Path) -> None:
    """Every alarm has a deadband (1 % of span, at most a quarter of a deviation
    limit, none for state alarms) and an off-delay; an explicit value wins."""
    full = load(DEFAULT_PLANT, "full")
    for a in full.alarms:
        assert a.off_delay_s == 0.0
        if a.kind == "state":
            assert a.deadband == 0.0
        else:
            assert a.deadband > 0.0, a.name
        if a.kind == "deviation":
            assert a.deadband <= a.limit / 4 + 1e-9, a.name

    def mutate(d: dict[str, Any]) -> None:
        tag = next(t for t in unit(d, "CEL", "FV101")["tags"] if t["id"] == "TT01.PV")
        tag["alarms"][0].update({"deadband": 0.3, "off_delay": "2m"})

    custom = load(write_plant(tmp_path, mutate), "ci")
    alarm = next(a for a in custom.alarms if a.name == "CEL.FV101.TT01.ALM_DEVIATION")
    assert (alarm.deadband, alarm.off_delay_s) == (0.3, 120.0)

    def bad(d: dict[str, Any]) -> None:
        tag = next(t for t in unit(d, "CEL", "FV101")["tags"] if t["id"] == "TT01.PV")
        tag["alarms"][0]["deadband"] = -1.0

    with pytest.raises(PlantError, match="deadband"):
        load(write_plant(tmp_path, bad), "ci")


def test_peer_reads_land_on_the_other_plcs_coils() -> None:
    """plc-utilities reads plc-cellar's transfer and CIP-permit bits over CTRL
    (DESIGN.md 6.6 #4): FC1 from the coils plc-cellar's modbus_slave serves, into
    %IX120.0 up, and a lost peer reads 0."""
    full = load(DEFAULT_PLANT, "full")
    alloc = allocate(full)
    reads = {r.tag: r for r in alloc.peer_reads["plc-utilities"]}
    assert set(reads) == {"UTL.CIP.XS03.PV", "UTL.CIP.XS04.PV"}
    for read in reads.values():
        src = alloc.tags[read.source]
        assert (src.slave_space, src.slave_address) == ("coil", read.coil)
        assert src.plc_iec.area == "Q" and read.iec.area == "I" and read.iec.index >= 960
    files = render_all(full, alloc)
    peer = next(d for d in json.loads(files["openplc/plc-utilities/modbus_master.json"]) if d["name"] == "plc-cellar")
    assert peer["config"]["host"] == "10.20.10.12"
    assert [(p["fc"], p["error_handling"]) for p in peer["config"]["io_points"]] == [(1, "set-to-zero")]


def test_a_peer_read_must_name_another_plcs_bit(tmp_path: Path) -> None:
    def mutate(d: dict[str, Any]) -> None:
        unit(d, "UTL", "CIP")["tags"].append({"id": "XS09.PV", "desc": "bad", "peer": "UTL.CIP.XS02.ROUTE_OK"})

    with pytest.raises(PlantError, match="another PLC"):
        allocate(load(write_plant(tmp_path, mutate), "full"))
