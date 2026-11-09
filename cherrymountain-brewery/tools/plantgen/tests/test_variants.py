"""plantgen: the full and lite variants, and the outputs only they have."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from plantgen import checks
from plantgen.allocate import allocate
from plantgen.apps import bacnet_read_property
from plantgen.cli import DEFAULT_PLANT, generate
from plantgen.model import Plant, PlantError, load, load_file
from plantgen.render import render_all
from plantgen.wgkeys import derive, public_key, x25519

VARIANTS: list[str] = list(load_file(DEFAULT_PLANT).variants)
GENERATED: Path = DEFAULT_PLANT.parent / "generated"


@pytest.fixture(scope="module")
def full() -> Plant:
    """The full variant."""
    return load(DEFAULT_PLANT, "full")


@pytest.fixture(scope="module")
def full_files(full: Plant) -> dict[str, str]:
    """Rendered files of the full variant."""
    return render_all(full, allocate(full))


@pytest.mark.parametrize("variant", VARIANTS)
def test_every_variant_passes_every_check(variant: str) -> None:
    """Database and cross-file checks pass for ci, full, and lite (§17)."""
    plant = load(DEFAULT_PLANT, variant)
    alloc = allocate(plant)
    assert checks.plant_checks(plant, alloc) == []
    files = render_all(plant, alloc)
    assert checks.file_checks(plant, alloc, files) == []


@pytest.mark.parametrize("variant", VARIANTS)
def test_committed_outputs_are_current(variant: str) -> None:
    """generated/<variant> holds exactly what plantgen renders now."""
    files = generate(DEFAULT_PLANT, variant)
    root = GENERATED / variant
    have = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    assert have == set(files)
    for rel, text in files.items():
        assert (root / rel).read_text(encoding="utf-8") == text, rel


@pytest.mark.parametrize("variant", VARIANTS)
def test_every_tag_has_an_owner_and_alarms_are_rationalized(variant: str) -> None:
    """§17: every tag has an owner, every alarm has its rationalization."""
    plant = load(DEFAULT_PLANT, variant)
    for t in plant.tags:
        assert t.owner in plant.hosts, t.name
    for a in plant.alarms:
        assert a.priority and a.cause and a.consequence and a.action
        assert a.response_time_s > 0


def test_variant_sizes_match_the_design(full: Plant) -> None:
    """§11: full is 43 VMs, lite 37 (no SOC, no vendor access), ci 5."""
    sizes = {v: len(load(DEFAULT_PLANT, v).hosts) for v in VARIANTS}
    assert sizes == {"ci": 5, "full": 43, "lite": 37}
    lite = load(DEFAULT_PLANT, "lite")
    gone = set(full.hosts) - set(lite.hosts)
    assert gone == {
        "malcolm",
        "wazuh",
        "soc-analyst",
        "vendor-ws",
        "dmz-vpn",
        "dmz-jump",
    }


def test_ci_keeps_only_the_fermentation_cell() -> None:
    """The ci register map must not move: no BBTs or shared cellar equipment."""
    ci = load(DEFAULT_PLANT, "ci")
    assert {t.unit for t in ci.tags} == {"CLK", "FV101", "FV102", "FV103", "FV104"}


def test_fv_addresses_are_the_same_in_ci_and_full(full: Plant) -> None:
    """New cellar units are appended, so FV registers keep their numbers."""
    ci = load(DEFAULT_PLANT, "ci")
    a_ci, a_full = allocate(ci), allocate(full)
    for name in ("CEL.FV101.TT01.PV", "CEL.FV104.ZSO01.PV", "CEL.FV103.PCV01.SP"):
        ci_a, full_a = a_ci.tags[name], a_full.tags[name]
        assert (ci_a.rio_space, ci_a.rio_address, str(ci_a.plc_iec)) == (
            full_a.rio_space,
            full_a.rio_address,
            str(full_a.plc_iec),
        )


# -- ot-sim app block -----------------------------------------------------------


def test_otsim_devices_name_their_federate_endpoint(full_files: dict[str, str]) -> None:
    """Devices outside the default federate set ``endpoint: updates``, which the
    app resolves against the device's own federate."""
    app = yaml.safe_load(full_files["phenix/ot-sim.yaml"])
    default = app["metadata"]["helics"]["federate"]
    for host in app["hosts"]:
        proto = host["metadata"].get("modbus") or host["metadata"].get("bacnet")
        if not proto:
            continue
        for dev in proto["devices"]:
            fed = dev["name"].split("/")[0]
            assert (dev.get("endpoint") == "updates") == (fed != default), dev


def test_signed_points_are_int16(full_files: dict[str, str]) -> None:
    """BESS and service power are int16 registers; the PLC declares INT."""
    app = yaml.safe_load(full_files["phenix/ot-sim.yaml"])
    infra = app["metadata"]["infrastructures"]["brewery"]
    assert infra["bess-pcs"]["p_kw"]["modbus"]["data-type"] == "int16"
    assert infra["bess-pcs"]["soc_pct"]["modbus"]["data-type"] == "uint16"
    st = full_files["openplc/plc-power/located_vars.st"]
    assert "PWR_BESS_JT01_PV AT %IW109 : INT;" in st


def test_bacnet_host_metadata(full_files: dict[str, str]) -> None:
    """BAS controllers carry the proposed bacnet host metadata (§16)."""
    app = yaml.safe_load(full_files["phenix/ot-sim.yaml"])
    vent = next(h for h in app["hosts"] if h["hostname"] == "bas-vent")
    assert vent["metadata"]["bacnet"]["interface"] == "IF0:47808"
    assert vent["metadata"]["bacnet"]["instance"] == 3003
    infra = app["metadata"]["infrastructures"]["brewery"]
    assert infra["vent"]["co2_1_ppm"]["bacnet"] == {
        "object-type": "analog-input",
        "units": "partsPerMillion",
    }
    assert infra["vent"]["purge_fan_1"]["bacnet"]["commandable"] is True


def test_fep_and_dnp3_master(full_files: dict[str, str]) -> None:
    """rtu-der polls the DER devices and serves DNP3; utility-derms polls it."""
    app = yaml.safe_load(full_files["phenix/ot-sim.yaml"])
    hosts = {h["hostname"]: h["metadata"] for h in app["hosts"]}
    assert hosts["rtu-der"]["upstream"] == ["relay-main", "pv-inverter", "bess-pcs"]
    assert hosts["rtu-der"]["dnp3"] == {"interface": "IF0:20000"}
    assert hosts["utility-derms"] == {
        "type": "fd-client",
        "connected_rtus": ["rtu-der"],
    }
    dnp3 = json.loads(full_files["dnp3/rtu-der.json"])
    kinds = [p["kind"] for p in dnp3["points"]]
    for kind in ("ai", "ao", "bi", "bo"):
        idx = [p["index"] for p in dnp3["points"] if p["kind"] == kind]
        assert idx == list(range(len(idx))), kind
    # msb + relay, pv-inverter, bess-pcs (with the BMS cell temperature and off-gas)
    assert len(kinds) == 6 + 9 + 6 + 9


# -- OpenPLC ------------------------------------------------------------------------


def test_externally_written_holding_registers_are_read_with_fc3(
    full_files: dict[str, str],
) -> None:
    """The utility limit is written by rtu-der, so plc-power reads it (FC3)
    into %IW and never writes it back with FC16."""
    cfg = json.loads(full_files["openplc/plc-power/modbus_master.json"])
    pv = next(d for d in cfg if d["name"] == "pv-inverter")
    blocks = [
        (p["fc"], p["offset"], p["iec_location"], p["len"])
        for p in pv["config"]["io_points"]
    ]
    assert (16, "40001", "%QW100", 1) in blocks
    assert (3, "40002", "%IW108", 1) in blocks


def test_opcua_and_s7_plcs_have_no_modbus_slave(full_files: dict[str, str]) -> None:
    """plc-brewhouse (OPC UA) and plc-packaging (S7) keep Modbus only south."""
    assert "openplc/plc-brewhouse/modbus_slave.json" not in full_files
    assert "openplc/plc-packaging/modbus_slave.json" not in full_files
    assert "openplc/plc-cellar/modbus_slave.json" in full_files
    fuxa = json.loads(full_files["fuxa/scada-fuxa-tags.json"])
    types = {d["name"]: d["type"] for d in fuxa["devices"]}
    assert types["plc-brewhouse"] == "OPCUA"
    assert types["plc-packaging"] == "SiemensS7"
    bh = next(d for d in fuxa["devices"] if d["name"] == "plc-brewhouse")
    tag = next(t for t in bh["tags"] if t["name"] == "BH.KTL.TT01.PV")
    assert tag["variable"] == "BH_KTL_TT01_PV" and tag["address"] is None


# -- FUXA and BACnet ----------------------------------------------------------------


def test_scada_fuxa_bacnet_leaves_broadcast_unset(full_files: dict[str, str]) -> None:
    """scada-fuxa is off the BAS subnet: Who-Is must go to 255.255.255.255.

    The OPS BBMD hears that limited broadcast and forwards it across fw-ot
    (spikes/bbmd); a directed broadcast of FUXA's own subnet would not reach
    the BAS controllers.
    """
    doc = json.loads(full_files["fuxa/scada-fuxa-tags.json"])
    bacnet = [d for d in doc["devices"] if d["type"] == "BACnet"]
    assert bacnet, "scada-fuxa reads BAS points over BACnet"
    for dev in bacnet:
        assert "broadcastAddress" not in dev["property"]


def test_bas_fuxa_reads_every_bacnet_object(
    full: Plant, full_files: dict[str, str]
) -> None:
    """bas-fuxa: one BACnet client device; memaddress is the remote device."""
    doc = json.loads(full_files["fuxa/bas-fuxa-tags.json"])
    (dev,) = doc["devices"]
    assert dev["type"] == "BACnet"
    assert dev["property"] == {
        "address": "10.30.10.10",
        "broadcastAddress": "10.30.10.255",
    }
    tags = {t["name"]: t for t in dev["tags"]}
    assert len(tags) == sum(1 for t in full.tags if t.bacnet)
    co2 = tags["FAC.VENT.AT01.PV"]
    assert (co2["address"], co2["memaddress"]) == ("0-1", "3003")
    assert tags["FAC.VENT.FAN01.CMD"]["access"] == "read-write"


def test_scada_fuxa_reads_cellar_co2_through_the_bbmds(
    full_files: dict[str, str],
) -> None:
    """§6.6 #6: scada-fuxa reads only bas-vent's CO2 objects."""
    doc = json.loads(full_files["fuxa/scada-fuxa-tags.json"])
    bac = next(d for d in doc["devices"] if d["type"] == "BACnet")
    names = {t["name"] for t in bac["tags"]}
    assert "FAC.VENT.AT04.PV" in names and "FAC.VENT.AY01.TIER2" in names
    assert not any(n.startswith(("FAC.RTU1", "FAC.CR")) for n in names)
    assert all(t["memaddress"] == "3003" for t in bac["tags"])
    assert bac["property"]["address"] == "10.20.30.10"


def test_bacnet_object_table(full_files: dict[str, str]) -> None:
    """Instances count from 1 per object type, in point order."""
    doc = json.loads(full_files["bacnet/bas-vent.json"])
    assert doc["device"] == {"instance": 3003, "name": "BAS-VENT-1"}
    assert (
        doc["endpoint"]["address"] == "10.30.10.22" and doc["endpoint"]["prefix"] == 24
    )
    ai = [o["instance"] for o in doc["objects"] if o["object_type"] == "analog-input"]
    # The four cellar sensors, the MUA discharge, then the three areas beyond
    # the cellar (DESIGN.md 6.7), appended so no cellar object moves.
    assert ai == [1, 2, 3, 4, 5, 6, 7, 8]
    tank = next(o for o in doc["objects"] if o["name"] == "FAC.VENT.AT05.PV")
    assert (tank["object_type"], tank["instance"]) == ("analog-input", 6)
    fan = next(o for o in doc["objects"] if o["name"] == "FAC.VENT.FAN01.CMD")
    assert fan["commandable"] and fan["object_type"] == "binary-output"


def test_bas_alarms_are_evaluated_by_fuxa(full_files: dict[str, str]) -> None:
    """BACnet alarms have no PLC bit; CO2 >= 30,000 ppm is Critical (§6.5)."""
    alarms = {a["name"]: a for a in json.loads(full_files["alarms.json"])["alarms"]}
    crit = alarms["FAC.VENT.AT01.ALM_HIGHHIGH"]
    assert crit["priority"] == "critical" and crit["limit"] == 30000.0
    assert crit["evaluator"] == "fuxa" and crit["plc_iec"] is None
    assert crit["owner"] == "bas-vent"
    assert alarms["FAC.VENT.AT01.ALM_HIGH"]["limit"] == 15000.0
    held = alarms["PKG.LINE.PACKML.ALM_STATE"]
    assert held["limit"] == 11.0 and held["delay_s"] == 600.0
    jam = alarms["PKG.LABELER.YA01.ALM_STATE"]
    assert jam["suppress"] == "PKG.LINE.XS01.RUN"


# -- HELICS -------------------------------------------------------------------------


def test_power_subscribes_to_every_load(full_files: dict[str, str]) -> None:
    """§9.2: the power federate subscribes to each load plantgen enumerates;
    each publisher follows its bus."""
    doc = json.loads(full_files["helics.json"])
    power = doc["federates"]["power"]
    keys = {s["key"] for s in power["subscriptions"]}
    for entry in power["loads"]:
        assert entry["key"] in keys
    assert sum(e["rated_kw"] for e in power["loads"]) == 329  # §7.2
    utl = {s["key"] for s in doc["federates"]["utilities"]["subscriptions"]}
    assert {"power/bus.boiler.energized", "power/bus.ep1.energized"} <= utl
    assert checks.helics_closed(load(DEFAULT_PLANT, "full"), full_files) == []
    assert doc["clock"]["start"].startswith("2026-07-15T04:30")
    assert doc["ground_truth"]["endpoint"] == "http://172.16.10.1:9200"


def test_helics_lists_are_closed_in_every_variant() -> None:
    """Every subscription has a publisher, every RIO write is accepted."""
    for variant in VARIANTS:
        plant = load(DEFAULT_PLANT, variant)
        files = render_all(plant, allocate(plant))
        assert checks.helics_closed(plant, files) == [], variant


def test_detects_unpublished_subscription(
    full: Plant, full_files: dict[str, str]
) -> None:
    """A subscription nobody publishes is caught."""
    doc: dict[str, Any] = json.loads(full_files["helics.json"])
    doc["federates"]["cellar"]["subscriptions"].append(
        {"key": "utilities/nothing", "type": "double", "units": None}
    )
    files = dict(full_files, **{"helics.json": json.dumps(doc)})
    assert checks.helics_closed(full, files) == [
        "HELICS: no publisher for utilities/nothing"
    ]


# -- soh, wireguard, scorch -----------------------------------------------------------


def test_bacnet_read_property_packet() -> None:
    """The DESIGN.md §15.4 probe for Device 3003 object-name."""
    assert bacnet_read_property(3003) == "gQoAEQEEAAUBDAwCAAu7GU0="


def test_soh_flows(full_files: dict[str, str]) -> None:
    """SoH tests each PLC to its field devices, FUXA to what it polls, DNP3,
    and the explicit conduits; VyOS hosts are skipped."""
    soh = yaml.safe_load(full_files["phenix/soh.yaml"])["metadata"]
    flows = {
        (f["src"], f["dst"], f["proto"], f["port"])
        for f in soh["testCustomReachability"]
    }
    assert ("plc-cellar", "rio-cellar|IF0", "tcp", 502) in flows
    assert ("plc-power", "bess-pcs|IF0", "tcp", 502) in flows
    assert ("scada-fuxa", "plc-brewhouse|IF0", "tcp", 4840) in flows
    assert ("scada-fuxa", "plc-packaging|IF0", "tcp", 102) in flows
    assert ("utility-derms", "rtu-der|IF0", "tcp", 20000) in flows
    assert ("scada-fuxa", "bas-vent|IF0", "udp", 47808) in flows
    assert ("dmz-jump", "ews|IF0", "tcp", 3389) in flows
    assert {"fw-ot", "fw-edge", "isp-rtr", "util-rtr"} <= set(soh["skipHosts"])
    assert soh["hostProcesses"]["sim-ctl"] == ["cherrymountain_sim", "helics_broker"]


def test_lite_soh_drops_soc_and_vendor_flows() -> None:
    """lite has no wazuh, malcolm, dmz-jump: their flows are dropped."""
    plant = load(DEFAULT_PLANT, "lite")
    files = render_all(plant, allocate(plant))
    soh = yaml.safe_load(files["phenix/soh.yaml"])["metadata"]
    for f in soh["testCustomReachability"]:
        assert f["src"] in plant.hosts and f["dst"].split("|")[0] in plant.hosts
    assert "phenix/wireguard.yaml" not in files


def test_x25519_rfc7748_vectors() -> None:
    """RFC 7748 §5.2 and §6.1 test vectors."""
    k = bytes.fromhex(
        "a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4"
    )
    u = bytes.fromhex(
        "e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c"
    )
    assert x25519(k, u).hex() == (
        "c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552"
    )
    alice = bytes.fromhex(
        "77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a"
    )
    pub = base64.b64encode(alice).decode()
    assert base64.b64decode(public_key(pub)).hex() == (
        "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a"
    )


def test_wireguard_keys_pair_up(full_files: dict[str, str]) -> None:
    """Each side's peer key is the other side's public key."""
    app = yaml.safe_load(full_files["phenix/wireguard.yaml"])
    hosts = {h["hostname"]: h["metadata"] for h in app["hosts"]}
    server, client = hosts["dmz-vpn"], hosts["vendor-ws"]
    assert server["peers"][0]["public_key"] == public_key(
        client["interface"]["private_key"]
    )
    assert client["peers"][0]["public_key"] == public_key(
        server["interface"]["private_key"]
    )
    assert client["peers"][0]["allowed_ips"] == "10.10.20.11/32"
    assert client["peers"][0]["server_endpoint"] == "203.0.113.2:51820"
    assert derive("seed", "a") == derive("seed", "a") != derive("seed", "b")


def test_scorch_capture_lists(full_files: dict[str, str]) -> None:
    """§15.6 capture VMs and interfaces per run."""
    runs = yaml.safe_load(full_files["scorch/pcap.yaml"])["runs"]
    vms = runs["utility-outage"]["metadata"]["vms"]
    assert {"hostname": "plc-power", "interface": "IF1"} in vms
    assert runs["cellar-co2-leak"]["type"] == "pcap"


# -- failure detection ----------------------------------------------------------------


def write_plant(tmp_path: Path, data: dict[str, Any]) -> Path:
    """Write ``data`` as a plant.yaml and return its path."""
    out = tmp_path / "plant.yaml"
    out.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return out


def raw() -> dict[str, Any]:
    """plant.yaml as data."""
    return yaml.safe_load(DEFAULT_PLANT.read_text(encoding="utf-8"))


def test_detects_duplicate_ip(tmp_path: Path) -> None:
    """Two hosts with one address are caught."""
    data = raw()
    data["hosts"]["bas-vent"]["interfaces"]["IF0"]["ip"] = "10.30.10.21/24"
    with pytest.raises(PlantError, match=r"10\.30\.10\.21 is also on"):
        generate(write_plant(tmp_path, data), "full")


def test_detects_address_outside_its_vlan(tmp_path: Path) -> None:
    """An address outside the VLAN's subnet is caught."""
    data = raw()
    data["hosts"]["ews"]["interfaces"]["IF0"]["ip"] = "10.20.31.12/24"
    with pytest.raises(PlantError, match="is not in OPS"):
        generate(write_plant(tmp_path, data), "full")


def test_detects_signed_range_overflow(tmp_path: Path) -> None:
    """A signed range that does not fit int16 after scaling is caught."""
    data = raw()
    area = next(a for a in data["areas"] if a["id"] == "PWR")
    bess = next(u for u in area["units"] if u["id"] == "BESS")
    bess["tags"][0]["range"] = [-5000, 150]
    with pytest.raises(PlantError, match="int16"):
        generate(write_plant(tmp_path, data), "full")


def test_detects_unknown_suppression_tag(tmp_path: Path) -> None:
    """State-based suppression must name an existing bit of the same owner."""
    data = raw()
    area = next(a for a in data["areas"] if a["id"] == "UTL")
    stm = next(u for u in area["units"] if u["id"] == "STM")
    stm["tags"][0]["alarms"][0]["suppress"] = "HS09.CMD"
    with pytest.raises(PlantError, match="suppression tag UTL.STM.HS09.CMD"):
        generate(write_plant(tmp_path, data), "full")


def test_detects_unreachable_ntp_source(tmp_path: Path) -> None:
    """A FIELD-only device cannot use ot-svc as its NTP source."""
    data = raw()
    data["variants"]["full"]["ntp"]["roles"]["rio"] = {"client": "chrony"}
    with pytest.raises(PlantError, match="rio-brewhouse cannot reach ot-svc"):
        generate(write_plant(tmp_path, data), "full")


def test_bacnet_point_must_fit_its_object_type(tmp_path: Path) -> None:
    """A binary-input object cannot carry a writable point."""
    data = raw()
    data["device_types"]["coldroom"]["door"]["type"] = "binary-read-write"
    with pytest.raises(PlantError, match="binary-input cannot carry"):
        load_file(write_plant(tmp_path, data))


@pytest.mark.parametrize("variant", VARIANTS)
def test_cherrymountain_app_block_matches_the_app_fixtures(variant: str) -> None:
    """phenix/cherrymountain.yaml has the shape the app's tests use."""
    plant = load(DEFAULT_PLANT, variant)
    app = yaml.safe_load(
        render_all(plant, allocate(plant))["phenix/cherrymountain.yaml"]
    )
    assert app["name"] == "cherrymountain"
    assert app["metadata"]["variant"] == variant
    hosts = {h["hostname"]: h["metadata"]["type"] for h in app["hosts"]}
    assert hosts["sim-ctl"] == "sim-ctl" and hosts["scada-fuxa"] == "fuxa"
    assert set(hosts) <= set(plant.hosts)
    fixture = (
        DEFAULT_PLANT.parents[1]
        / "apps"
        / "cherrymountain_apps"
        / (f"cherrymountain/tests/fixtures/app-{variant}.yml")
    )
    if fixture.exists():
        want = yaml.safe_load(fixture.read_text(encoding="utf-8"))
        assert app["metadata"] == want["metadata"]
        got = {h["hostname"]: h["metadata"] for h in app["hosts"]}
        assert got == {h["hostname"]: h["metadata"] for h in want["hosts"]}


# -- truth outputs, relief valves, and the ground-truth snapshot ---------------


def test_unit_outputs_follow_the_variant_units(full_files: dict[str, str]) -> None:
    """``unit_outputs`` publish one key per unit of the listed models: the
    fermenters' fill in every variant, the relief-valve vent on the bright
    tanks too, and only for units the variant keeps."""
    cellar = json.loads(full_files["helics.json"])["federates"]["cellar"]
    keys = {p["key"]: p for p in cellar["publications"]}
    for unit in ("fv101", "fv102", "fv103", "fv104"):
        for name in ("level_pct", "spund_gps", "psv_gps", "leak_gps"):
            assert keys[f"cellar/{unit}.{name}"]["output"] == f"{unit}.{name}"
    assert "cellar/bbt201.psv_gps" in keys and "cellar/bbt201.spund_gps" not in keys
    assert keys["cellar/bbt201.level_pct"]["device"] == "bbt201"  # LT01, not truth
    ci = load(DEFAULT_PLANT, "ci")
    ci_keys = {
        p["key"]
        for p in json.loads(render_all(ci, allocate(ci))["helics.json"])["federates"][
            "cellar"
        ]["publications"]
    }
    assert "cellar/fv101.level_pct" in ci_keys
    assert not any(k.startswith("cellar/bbt") for k in ci_keys)


def test_relief_valve_lift_switches_are_full_plant_only(full: Plant) -> None:
    """Six lift switches in the appended PSV unit, each with a High alarm; ci
    keeps its register map (test_fv_addresses_are_the_same_in_ci_and_full)."""
    psv = [t for t in full.tags if t.unit == "PSV" and not t.generated]
    assert [t.device.zone for t in psv if t.device] == [
        "FV101", "FV102", "FV103", "FV104", "BBT201", "BBT202",
    ]
    assert all(t.alarms[0].kind == "state" and t.alarms[0].priority == "high" for t in psv)
    assert not [t for t in load(DEFAULT_PLANT, "ci").tags if t.unit == "PSV"]


def test_snapshot_subscribes_the_weather_federate(full_files: dict[str, str]) -> None:
    """plant.yaml ``snapshot`` expands to the weather federate's subscriptions:
    the KPI keys and the 3D view's keys, each published by another federate,
    none of the weather federate's own."""
    doc = json.loads(full_files["helics.json"])
    subs = {s["key"] for s in doc["federates"]["weather"]["subscriptions"]}
    published = {
        p["key"]
        for name, fed in doc["federates"].items()
        if name != "weather"
        for p in fed["publications"]
    }
    assert subs <= published
    assert {
        "brewhouse/batch.completed",
        "packaging/bottles.total",
        "power/energy.kwh",
        "power/demand.peak_kw",
        "building/cellar.co2_ppm",
        "building/bas-vent.purge_fan_1_status",
        "cellar/fv103-psv.lifted",
        "cellar/fv103.level_pct",
        "power/gen-ats.on_emergency",
        "building/load.fans.kw",
    } <= subs
    assert not any(k.startswith("weather/") for k in subs)
