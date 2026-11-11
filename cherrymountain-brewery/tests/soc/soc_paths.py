"""Paths, fixtures, and helpers shared by the SOC and remote-access tests.

A module of its own (not a conftest.py), because the netcheck tests do ``from conftest
import ...``, which would find this directory's conftest first when both run in one
pytest session. The tests import the fixtures from here.
"""

from __future__ import annotations

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
