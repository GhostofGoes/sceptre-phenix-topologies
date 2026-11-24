"""Paths, fixtures, and helpers shared by the SOC and remote-access tests.

A module of its own (not a conftest.py), because the netcheck tests do ``from conftest
import ...``, which would find this directory's conftest first when both run in one
pytest session. The tests import the fixtures from here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

ROOT: Path = Path(__file__).resolve().parents[2]
INJECTS: Path = ROOT / "phenix-injects"
CONFIGS: Path = ROOT / "phenix-configs"

# The injected Python scripts are plain modules; tests import them from here.
for _directory in ("wireguard", "desktop", "ews", "malcolm", "guacamole"):
    sys.path.insert(0, str(INJECTS / _directory))


def app_dir() -> Path:
    """The ``cherrymountain`` phenix app's directory in the installed phenix-apps
    package. The app is in sceptre-phenix-apps (``phenix_apps.apps.cherrymountain``),
    not in this repository; the tests that read its files need the package the
    range's phenix image is built with. Only ``phenix_apps`` itself is imported."""
    spec = importlib.util.find_spec("phenix_apps")
    if spec is None or not spec.submodule_search_locations:
        raise ModuleNotFoundError(
            "phenix-apps is not installed: these tests read the cherrymountain app's "
            "files from it (GhostofGoes/sceptre-phenix-apps, branch "
            "feat-cherrymountain-apps, src/python)"
        )
    path: Path = Path(next(iter(spec.submodule_search_locations))) / "apps" / "cherrymountain"
    if not (path / "render.py").is_file():
        raise ModuleNotFoundError(
            f"the installed phenix-apps ({path.parents[1]}) has no cherrymountain app: "
            "install the feat-cherrymountain-apps branch of GhostofGoes/sceptre-phenix-apps"
        )
    return path


def node_ip(nodes: dict[str, dict], hostname: str, interface: str = "IF0") -> str:
    for iface in nodes[hostname]["network"]["interfaces"]:
        if iface["name"] == interface:
            return str(iface["address"])
    raise KeyError(f"{hostname} has no {interface}")


@pytest.fixture(scope="session")
def repo() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def topology_nodes() -> dict[str, dict]:
    """Every node of the core, soc, and remote-access topologies, by hostname."""
    nodes: dict[str, dict] = {}
    for name in ("core", "soc", "remote-access"):
        doc = yaml.safe_load((CONFIGS / f"topology-{name}.yml").read_text())
        for node in doc["spec"]["nodes"]:
            nodes[node["general"]["hostname"]] = node
    return nodes
