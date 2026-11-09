"""The scenario's user apps, run one after the other as phenix runs them.

Every other stage test runs the cherrymountain app alone. Here each variant's
experiment is built from the committed workflow, topology, and scenario (the
scenario's own app blocks, not the fixture copies), and the real phenix user
apps that the scenario lists run on it in scenario order: configure once, then
pre-start twice, since phenix keeps a start's injections in the stored
experiment and the next start runs on them. The Go ``mirror`` app and phenix's
own apps (startup, ntp, vrouter, soh, scorch) are not run.
"""

from __future__ import annotations

import io
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any, Final

import pytest
import yaml
from phenix_apps.apps import AppBase
from phenix_apps.apps.helics.app import Helics
from phenix_apps.apps.otsim.app import OTSim
from phenix_apps.apps.protonuke.app import Protonuke
from phenix_apps.apps.wireguard.app import Wireguard

from cherrymountain_apps.cherrymountain.app import CherryMountain
from cherrymountain_apps.cherrymountain.tests.conftest import ROOT

#: variant -> (workflow file, the branch ``phenix workflow apply`` is given).
VARIANTS: Final[dict[str, tuple[str, str]]] = {
    "ci": ("phenix-ci.yml", "cherrymountain-ci"),
    "lite": ("phenix-lite.yml", "cherrymountain-lite"),
    "full": ("phenix.yml", "cherrymountain-brewery"),
}
#: The scenario apps that are phenix-apps (Python) user apps.
USER_APPS: Final[dict[str, type[AppBase]]] = {
    "ot-sim": OTSim,
    "cherrymountain": CherryMountain,
    "helics": Helics,
    "protonuke": Protonuke,
    "wireguard": Wireguard,
}
OTSIM_CONFIG: Final[str] = "/etc/ot-sim/config.xml"


def load(path: Path, branch: str) -> Any:
    """A config as the workflow stores it: ``${BRANCH_NAME}`` expanded, and the
    injects directory, which the apply copies to /phenix/injects/<branch>,
    read from the repository."""
    text: str = path.read_text(encoding="utf-8").replace("${BRANCH_NAME}", branch)
    return yaml.safe_load(
        text.replace(f"/phenix/injects/{branch}", str(ROOT / "phenix-injects"))
    )


def configs(kind: str, branch: str) -> dict[str, Any]:
    """Every committed config of a kind, by its metadata name."""
    found: dict[str, Any] = {}
    for path in sorted((ROOT / "phenix-configs").glob(f"{kind}*.yml")):
        doc: Any = load(path, branch)
        found[doc["metadata"]["name"]] = doc
    return found


def topology_nodes(name: str, topologies: dict[str, Any]) -> list[Any]:
    """The topology's nodes after its includeTopologies, in phenix's order."""
    spec: Any = topologies[name]["spec"]
    nodes: list[Any] = []
    for included in spec.get("includeTopologies") or []:
        nodes += topology_nodes(included, topologies)
    return nodes + list(spec.get("nodes") or [])


def scenario_apps(name: str, scenarios: dict[str, Any]) -> list[Any]:
    """The scenario's apps, with each ``fromScenario`` app taken from the
    scenario it names, as phenix does."""
    apps: list[Any] = []
    for app in scenarios[name]["spec"]["apps"]:
        source: str | None = app.get("fromScenario")
        if source:
            app = next(
                a for a in scenario_apps(source, scenarios) if a["name"] == app["name"]
            )
        apps.append(app)
    return apps


def experiment(variant: str, base_dir: Path) -> dict[str, Any]:
    wf_file, branch = VARIANTS[variant]
    workflow: Any = load(ROOT / wf_file, branch)["spec"]
    apps: list[Any] = scenario_apps(workflow["scenario"], configs("scenario", branch))
    for app in apps:
        if app["name"] == "cherrymountain":
            app["metadata"]["sourceDir"] = str(ROOT)
    return {
        "apiVersion": "phenix.sandia.gov/v1",
        "kind": "Experiment",
        "metadata": {
            "name": branch,
            "annotations": {
                "topology": workflow["topology"],
                "scenario": workflow["scenario"],
            },
        },
        "spec": {
            "experimentName": branch,
            "baseDir": str(base_dir),
            "defaultBridge": workflow["defaultBridge"],
            "topology": {
                "nodes": topology_nodes(
                    workflow["topology"], configs("topology", branch)
                )
            },
            "scenario": {"apps": apps},
            "vlans": {"aliases": workflow["vlans"], "min": 100, "max": 199},
            "schedules": {},
            "deployMode": "no-headnode",
            "useGREMesh": False,
        },
        "status": {"startTime": "", "schedules": {}, "apps": {}, "vlans": {}},
    }


class Chain:
    """One variant's experiment after configure and two starts."""

    def __init__(self, variant: str, base_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.variant: str = variant
        self.base_dir: Path = base_dir
        # phenix's startup app, which runs before every user app, makes this.
        (base_dir / "startup").mkdir(parents=True)
        self.exp: dict[str, Any] = experiment(variant, base_dir)
        self.order: list[str] = [
            a["name"] for a in self.exp["spec"]["scenario"]["apps"] if a["name"] in USER_APPS
        ]
        self.starts: list[dict[str, Any]] = []
        self._run("configure", monkeypatch)
        for _ in range(2):
            self._run("pre-start", monkeypatch)
            self.starts.append(json.loads(json.dumps(self.exp)))

    def _run(self, stage: str, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in self.order:
            monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(self.exp)))
            app: AppBase = USER_APPS[name](name, stage)
            try:
                app.execute_stage()
            except SystemExit as ex:  # AppBase logs the error, then exits 1
                raise AssertionError(
                    f"{self.variant}: the {name} app failed its {stage} stage"
                ) from ex
            # What phenix reads back from the app's standard output.
            self.exp = json.loads(app.experiment.to_json())

    def nodes(self, start: int = -1) -> dict[str, Any]:
        return {
            n["general"]["hostname"]: n
            for n in self.starts[start]["spec"]["topology"]["nodes"]
        }

    def app(self, name: str, start: int = -1) -> Any:
        return next(
            a for a in self.starts[start]["spec"]["scenario"]["apps"] if a["name"] == name
        )

    def injections(self, start: int = -1) -> dict[str, list[tuple[str, str, str]]]:
        return {
            host: sorted(
                (i["src"], i["dst"], i.get("permissions") or "")
                for i in node.get("injections") or []
            )
            for host, node in self.nodes(start).items()
        }

    def federate_hosts(self) -> dict[str, int]:
        """hostname -> its ``helics/federate`` fed-count."""
        return {
            host: sum(c.get("fed-count", 1) for c in node["annotations"]["helics/federate"])
            for host, node in self.nodes().items()
            if (node.get("annotations") or {}).get("helics/federate")
        }


@pytest.fixture(scope="module", params=sorted(VARIANTS))
def chain(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> Any:
    with pytest.MonkeyPatch.context() as monkeypatch:
        yield Chain(
            request.param, tmp_path_factory.mktemp(request.param) / "exp", monkeypatch
        )


def test_the_scenario_orders_the_apps_as_they_depend_on_each_other(chain: Chain) -> None:
    """ot-sim renders config.xml and annotates its I/O federates, this app
    builds on both and rekeys WireGuard, helics counts every annotation, and
    wireguard renders the new keys."""
    order: list[str] = chain.order
    assert order.index("ot-sim") < order.index("cherrymountain") < order.index("helics")
    if "wireguard" in order:
        assert order.index("cherrymountain") < order.index("wireguard")


def test_every_start_leaves_injections_minimega_can_use(chain: Chain) -> None:
    """Each source exists, no node gets two files for one destination, and no
    source is injected with two modes (phenix sets the mode on the source
    itself and refuses the start otherwise: CheckInjectionModes)."""
    for start in (0, 1):
        modes: dict[str, set[str]] = {}
        for host, injects in chain.injections(start).items():
            dsts: Counter[str] = Counter(dst for _, dst, _ in injects)
            twice: list[str] = [dst for dst, count in dsts.items() if count > 1]
            assert not twice, (chain.variant, start, host, twice)
            for src, dst, mode in injects:
                assert Path(src).is_file(), (chain.variant, start, host, dst)
                if mode:
                    modes.setdefault(src, set()).add(mode)
        clash: dict[str, set[str]] = {s: m for s, m in modes.items() if len(m) > 1}
        assert not clash, (chain.variant, start, clash)


def test_a_second_start_changes_nothing(chain: Chain) -> None:
    assert chain.injections(1) == chain.injections(0)
    assert chain.nodes(1).keys() == chain.nodes(0).keys()
    for host, node in chain.nodes(1).items():
        assert node.get("annotations") == chain.nodes(0)[host].get("annotations"), host


def test_the_broker_waits_for_every_federate_the_apps_annotated(chain: Chain) -> None:
    """The helics app starts the root broker on sim-ctl for the sum of the
    ``helics/federate`` annotations: this app's on the sim nodes and the ot-sim
    app's on every device with an I/O module. The SoH check on sim-ctl asks
    for the same hosts."""
    feds: dict[str, int] = chain.federate_hosts()
    script: str = (chain.base_dir / "helics" / "sim-ctl-broker.sh").read_text()
    waits_for: list[str] = re.findall(r"-f(\d+)\b", script)
    assert waits_for == [str(sum(feds.values()))], script

    otsim: Any = chain.app("ot-sim")
    io_modules: set[str] = set()
    for host in otsim["hosts"]:
        config: Path = chain.base_dir / "ot-sim" / f"{host['hostname']}.xml"
        if ET.parse(config).getroot().find("io") is not None:
            io_modules.add(host["hostname"])
    sims: set[str] = {h for h in feds if h.startswith("sim-")}
    assert io_modules and sims
    assert set(feds) == sims | io_modules

    sim_ctl: Any = next(
        h for h in chain.app("cherrymountain")["hosts"] if h["hostname"] == "sim-ctl"
    )
    tests: list[Any] = sim_ctl["metadata"]["sohProfile"]["customTests"]
    count: Any = next(t for t in tests if t["name"] == "helics-federates-connected")
    assert f"want={len(feds)}\n" in count["testScript"]


def test_every_ot_sim_device_keeps_one_config(chain: Chain) -> None:
    """A device with the telnet module (relay-main) gets this app's copy of
    the ot-sim app's config.xml, the others the ot-sim app's own."""
    cm: dict[str, Any] = {
        h["hostname"]: h["metadata"] for h in chain.app("cherrymountain")["hosts"]
    }
    for host in chain.app("ot-sim")["hosts"]:
        name: str = host["hostname"]
        sources: list[str] = [
            src for src, dst, _ in chain.injections()[name] if dst == OTSIM_CONFIG
        ]
        assert len(sources) == 1, (chain.variant, name)
        root: ET.Element = ET.parse(sources[0]).getroot()
        rendered: ET.Element = ET.parse(
            chain.base_dir / "ot-sim" / f"{name}.xml"
        ).getroot()
        modules: list[str | None] = [m.get("name") for m in root.iter("module")]
        expected: list[str | None] = [m.get("name") for m in rendered.iter("module")]
        if (cm.get(name) or {}).get("telnet"):
            assert modules == [*expected, "telnet"], (chain.variant, name)
            assert len(root.findall("telnet")) == 1
        else:
            assert modules == expected, (chain.variant, name)


def test_wireguard_renders_the_keys_this_app_generated(chain: Chain) -> None:
    """configure replaces the scenario's committed keys; the wireguard app,
    which runs later, renders them, and each peer still names the other end."""
    if "wireguard" not in chain.order:
        pytest.skip(f"the {chain.variant} variant has no wireguard app")
    sys.path.insert(0, str(ROOT / "tools" / "plantgen"))
    try:
        from plantgen.wgkeys import public_key
    finally:
        sys.path.remove(str(ROOT / "tools" / "plantgen"))

    committed: Any = yaml.safe_load(
        (ROOT / "phenix-configs" / f"scenario-{chain.variant}.yml").read_text()
    )
    old: set[str] = {
        h["metadata"]["interface"]["private_key"]
        for a in committed["spec"]["apps"]
        if a["name"] == "wireguard"
        for h in a["hosts"]
    }
    private: dict[str, str] = {}
    peers: dict[str, list[str]] = {}
    for host in chain.app("wireguard")["hosts"]:
        conf: str = (
            chain.base_dir / "startup" / f"{host['hostname']}-wireguard.conf"
        ).read_text()
        private[host["hostname"]] = re.findall(r"PrivateKey = (\S+)", conf)[0]
        peers[host["hostname"]] = re.findall(r"PublicKey = (\S+)", conf)
    assert len(private) == 2 and not old & set(private.values())
    public: dict[str, str] = {h: public_key(k) for h, k in private.items()}
    for host, named in peers.items():
        others: list[str] = [public[h] for h in public if h != host]
        assert named == others, host
    stored: Any = json.loads((chain.base_dir / "cherrymountain" / "wireguard.json").read_text())
    assert stored == private
