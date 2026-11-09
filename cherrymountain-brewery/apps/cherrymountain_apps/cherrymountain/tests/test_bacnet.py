"""The BAS controllers (bas-rtu-taproom, bas-coldroom, bas-vent).

The phenix ot-sim app knows only Modbus and DNP3, so it gives a BACnet
controller neither an I/O module nor a protocol module. The cherrymountain app
adds both (DESIGN.md 12.3, 16). These tests run the real apps in scenario
order on the recorded full experiment: ot-sim, then cherrymountain, then
helics.
"""

from __future__ import annotations

import io
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import lxml.etree
import pytest
from phenix_apps.apps.helics.app import Helics
from phenix_apps.apps.otsim.app import OTSim
from phenix_apps.apps.otsim.infrastructure import Infrastructure

from cherrymountain_apps.cherrymountain import render
from cherrymountain_apps.cherrymountain.app import CherryMountain
from cherrymountain_apps.cherrymountain.metadata import RioHost
from cherrymountain_apps.cherrymountain.render import Context
from cherrymountain_apps.cherrymountain.sources import SourceError
from cherrymountain_apps.cherrymountain.tests.conftest import ROOT, RunApp, node
from cherrymountain_apps.cherrymountain.tests.test_full import ctx, full  # noqa: F401 - fixtures

sys.path.insert(0, str(ROOT / "phenix-injects" / "otsim"))
import cm_otsim_bacnet  # noqa: E402 - the module the app injects

BAS: tuple[str, ...] = ("bas-rtu-taproom", "bas-coldroom", "bas-vent")
CONFIG: str = "/etc/ot-sim/config.xml"


def app_named(exp: dict[str, Any], name: str) -> dict[str, Any]:
    return next(a for a in exp["spec"]["scenario"]["apps"] if a["name"] == name)


def config_of(exp: dict[str, Any], hostname: str) -> ET.Element:
    """The config.xml that ends up on ``hostname``."""
    sources: list[str] = [i["src"] for i in node(exp, hostname)["injections"] if i["dst"] == CONFIG]
    assert len(sources) == 1, (hostname, sources)
    return ET.fromstring(Path(sources[0]).read_text())  # noqa: S314 - our own file


class Chain:
    """The experiment after each app's pre-start, in scenario order."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, exp: dict[str, Any]) -> None:
        def stage(cls: Any, name: str, doc: dict[str, Any]) -> dict[str, Any]:
            monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(doc)))
            app: Any = cls(name, "pre-start", False)
            app.pre_start()
            return dict(json.loads(app.experiment.to_json()))

        names: list[str] = [a["name"] for a in exp["spec"]["scenario"]["apps"]]
        assert names.index("ot-sim") < names.index("cherrymountain") < names.index("helics")
        self.base_dir: Path = Path(exp["spec"]["baseDir"])
        self.otsim: dict[str, Any] = stage(OTSim, "ot-sim", exp)
        self.cherrymountain: dict[str, Any] = stage(CherryMountain, "cherrymountain", self.otsim)
        self.helics: dict[str, Any] = stage(Helics, "helics", self.cherrymountain)


@pytest.fixture
def chain(monkeypatch: pytest.MonkeyPatch, full: dict[str, Any]) -> Chain:  # noqa: F811
    return Chain(monkeypatch, full)


def test_the_ot_sim_app_alone_leaves_a_bacnet_controller_without_io(chain: Chain) -> None:
    """Why this app renders the modules. When the ot-sim app learns BACnet
    this fails, and the rendering here can go."""
    for host in BAS:
        root: ET.Element = config_of(chain.otsim, host)
        assert [m.get("name") for m in root.find("cpu").findall("module")] == [  # type: ignore[union-attr]
            "backplane",
            "ground-truth",
        ]
        assert root.find("io") is None
        assert "helics/federate" not in (node(chain.otsim, host).get("annotations") or {})
    # A Modbus device, for contrast, has both.
    assert config_of(chain.otsim, "rio-cellar").find("io") is not None
    assert node(chain.otsim, "rio-cellar")["annotations"]["helics/federate"]


def test_bacnet_controllers_get_the_io_and_bacnet_modules(chain: Chain) -> None:
    for host in BAS:
        root: ET.Element = config_of(chain.cherrymountain, host)
        modules: dict[str | None, str | None] = {
            m.get("name"): m.text for m in root.find("cpu").findall("module")  # type: ignore[union-attr]
        }
        assert list(modules) == ["backplane", "ground-truth", "i/o", "bacnet"]
        assert modules["i/o"] == "ot-sim-io-module {{config_file}}"
        assert modules["bacnet"] == (
            "/opt/otsim-venv/bin/python -I /usr/local/lib/cm-otsim/cm_otsim_bacnet.py {{config_file}}"
        )
        assert len(root.findall("io")) == 1 and len(root.findall("bacnet")) == 1
        # What the ot-sim app rendered is still there.
        assert root.findtext("message-bus/pull-endpoint") == "tcp://127.0.0.1:1234"
        assert root.find("ground-truth") is not None


def test_the_io_element_is_what_the_ot_sim_app_renders_for_a_modbus_device(chain: Chain) -> None:
    """The subscriptions and the endpoint come out as the ot-sim app's own
    code gives them for the same infrastructure device, and the federate
    settings equal those of a device the ot-sim app rendered."""
    otsim: dict[str, Any] = app_named(chain.cherrymountain, "ot-sim")
    mappings: dict[str, Any] = otsim["metadata"]["infrastructures"]
    reference: ET.Element = config_of(chain.otsim, "rio-cellar").find("io")  # type: ignore[assignment]
    for host in BAS:
        host_md: dict[str, Any] = next(h for h in otsim["hosts"] if h["hostname"] == host)["metadata"]
        devices: dict[str, Any] = {
            d["name"]: {**d, "endpoint": f"{d['name'].split('/')[0]}/{d['endpoint']}"}
            for d in host_md["bacnet"]["devices"]
        }
        theirs: Any = lxml.etree.Element("io")
        Infrastructure(mappings).io_module_xml(theirs, otsim["metadata"]["infrastructure"], devices)
        want: ET.Element = ET.fromstring(lxml.etree.tostring(theirs))  # noqa: S314 - our own text
        ours: ET.Element = config_of(chain.cherrymountain, host).find("io")  # type: ignore[assignment]

        def subscriptions(io_element: ET.Element) -> list[tuple[str | None, ...]]:
            return [
                (s.findtext("key"), s.findtext("tag"), s.findtext("type"))
                for s in io_element.findall("subscription")
            ]

        def endpoints(io_element: ET.Element) -> dict[str | None, list[tuple[str | None, str | None]]]:
            return {
                e.get("name"): [(t.get("key"), t.text) for t in e.findall("tag")]
                for e in io_element.findall("endpoint")
            }

        assert subscriptions(ours) == subscriptions(want), host
        assert endpoints(ours) == endpoints(want), host
        assert ours.findall("publication") == []
        assert ours.get("name") == reference.get("name")
        assert ours.findtext("federate-name") == host
        for setting in ("broker-endpoint", "federate-log-level", "end-time"):
            assert ours.findtext(setting) == reference.findtext(setting), setting


def test_the_module_accepts_every_rendered_config(chain: Chain) -> None:
    """The bacnet element parses with the module's own settings model, its
    address is the node's, and its objects are plantgen's table."""
    otsim: dict[str, Any] = app_named(chain.cherrymountain, "ot-sim")
    for host in BAS:
        root: ET.Element = config_of(chain.cherrymountain, host)
        config: Any = cm_otsim_bacnet.BacnetConfig.from_xml(root)
        address: str = next(
            i["address"] for i in node(chain.cherrymountain, host)["network"]["interfaces"] if i["name"] == "IF0"
        )
        assert config.endpoint == f"{address}/24:47808"
        scenario: dict[str, Any] = next(h for h in otsim["hosts"] if h["hostname"] == host)["metadata"]["bacnet"]
        assert (config.device_instance, config.device_name) == (scenario["instance"], scenario["name"])
        assert (config.pull_endpoint, config.pub_endpoint) == ("tcp://127.0.0.1:1234", "tcp://127.0.0.1:5678")
        table: dict[str, Any] = json.loads((ROOT / "plant/generated/full/bacnet" / f"{host}.json").read_text())
        assert [(o.type, o.instance, o.name, o.tag, o.commandable) for o in config.objects] == [
            (o["object_type"], o["instance"], o["name"], o["tag"], o["commandable"]) for o in table["objects"]
        ]
        # Every object has a value to follow, and every command has a way out.
        io_element: ET.Element = root.find("io")  # type: ignore[assignment]
        subscribed: set[str | None] = {s.findtext("tag") for s in io_element.findall("subscription")}
        sent: set[str | None] = {t.text for t in io_element.iter("tag") if t.get("key")}
        assert {o.tag for o in config.objects} == subscribed
        assert {o.tag for o in config.objects if o.commandable} == sent
        for obj in config.objects:
            if obj.type == "multi-state-value":
                assert obj.states and obj.minimum is None


def test_the_occupancy_mode_and_a_setpoint_as_rendered(chain: Chain) -> None:
    config: Any = cm_otsim_bacnet.BacnetConfig.from_xml(config_of(chain.cherrymountain, "bas-rtu-taproom"))
    by_name: dict[str, Any] = {o.name: o for o in config.objects}
    mode: Any = by_name["FAC.RTU1.HS01.MODE"]
    assert (mode.type, mode.states, mode.commandable) == (
        "multi-state-value",
        ("occupied", "unoccupied", "standby"),
        True,
    )
    setpoint: Any = by_name["FAC.RTU1.TIC01.SP"]
    assert (setpoint.type, setpoint.units, setpoint.commandable) == ("analog-value", "degreesFahrenheit", True)
    assert (setpoint.minimum, setpoint.maximum) == (60.0, 90.0)
    assert not by_name["FAC.RTU1.TT01.PV"].commandable


def test_the_module_file_is_injected(chain: Chain) -> None:
    for host in BAS:
        (inject,) = [
            i for i in node(chain.cherrymountain, host)["injections"] if i["dst"] == render.BACNET_MODULE
        ]
        assert Path(inject["src"]).read_bytes() == (ROOT / "phenix-injects/otsim/cm_otsim_bacnet.py").read_bytes()
        assert inject["permissions"] == "0644"
    # A Modbus device does not get it.
    assert not [i for i in node(chain.cherrymountain, "rio-cellar")["injections"] if i["dst"] == render.BACNET_MODULE]


def test_the_helics_app_counts_the_bacnet_controllers(chain: Chain) -> None:
    """The helics app starts the root broker for as many federates as the
    helics/federate annotations add up to: every federate of helics.json."""
    for host in BAS:
        assert node(chain.cherrymountain, host)["annotations"]["helics/federate"] == [
            {"broker": "sim-ctl|mgmt", "fed-count": 1}
        ]
        assert [i for i in node(chain.helics, host)["injections"] if i["dst"].endswith("5-wait-broker.sh")]
    want: int = json.loads((ROOT / "plant/generated/full/helics.json").read_text())["federate_count"]
    script: str = (chain.base_dir / "helics" / "sim-ctl-broker.sh").read_text()
    assert f"-f{want} " in script.replace("\\\n", " "), script


def test_rendering_again_changes_nothing(ctx: Context) -> None:  # noqa: F811
    base: str = (
        '<ot-sim><cpu><module name="backplane">ot-sim-message-bus {{config_file}}</module></cpu></ot-sim>'
    )
    once: str = render.add_bacnet(base, render.io_element(ctx, "bas-vent"), render.bacnet_element(ctx, "bas-vent"))
    twice: str = render.add_bacnet(once, render.io_element(ctx, "bas-vent"), render.bacnet_element(ctx, "bas-vent"))
    assert once == twice
    assert once.startswith("<ot-sim>")  # no declaration: the DNP3 module rejects one


def test_a_bacnet_controller_without_an_ot_sim_config_fails_pre_start(ctx: Context) -> None:  # noqa: F811
    md: Any = ctx.hosts["bas-vent"]
    assert isinstance(md, RioHost)
    with pytest.raises(SourceError, match="ot-sim before cherrymountain"):
        render.render_otsim(ctx, "bas-vent", md, None)
    # A device that needs nothing from this app still gets nothing.
    assert render.render_otsim(ctx, "rio-cellar", ctx.hosts["rio-cellar"], None) == []  # type: ignore[arg-type]


def test_soh_checks_the_modules_and_the_listener(run_app: RunApp, full: dict[str, Any]) -> None:  # noqa: F811
    app = run_app("configure", exp=full)
    for host in BAS:
        profile: Any = next(h for h in app.app.hosts if h.hostname == host).metadata.sohProfile
        assert list(profile.processes) == ["ot-sim-cpu-module", "ot-sim-io-module", "cm_otsim_bacnet.py"]
        assert list(profile.listeners) == [":47808"]
    modbus: Any = next(h for h in app.app.hosts if h.hostname == "rio-cellar").metadata.sohProfile
    assert "cm_otsim_bacnet.py" not in modbus.processes
