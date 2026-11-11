#!/usr/bin/env python3
"""Record the experiment JSON fixtures from the ci topology and scenario.

phenix hands an app the whole experiment as JSON on stdin. This builds that
document the way phenix does for each variant: the topology spec from
phenix-configs/ (with ``includeTopologies`` expanded, as phenix does), the
scenario's apps with fixtures/app-<variant>.yml inserted before helics (after ot-sim
while there is no helics block), and
the workflow's VLAN map. Run it after a change to those files and commit the
result:

    python3 -I build_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

HERE: Path = Path(__file__).resolve().parent
ROOT: Path = HERE.parents[3]  # cherrymountain-brewery/
FIXTURES: Path = HERE / "fixtures"
#: variant -> (workflow file, topology file).
VARIANTS: dict[str, tuple[str, str]] = {
    "ci": ("phenix-ci.yml", "topology-ci.yml"),
    "full": ("phenix.yml", "topology-full.yml"),
}


def load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def topology_nodes(name: str) -> list[Any]:
    """The topology's nodes after its includeTopologies, in phenix's order."""
    by_name: dict[str, Path] = {}
    for path in (ROOT / "phenix-configs").glob("topology*.yml"):
        by_name[load(path)["metadata"]["name"]] = path
    spec: Any = load(by_name[name])["spec"]
    nodes: list[Any] = []
    for included in spec.get("includeTopologies") or []:
        nodes += topology_nodes(included)
    return nodes + list(spec.get("nodes") or [])


def experiment(variant: str) -> dict[str, Any]:
    wf_file, topo_file = VARIANTS[variant]
    topology: Any = load(ROOT / "phenix-configs" / topo_file)
    workflow: Any = load(ROOT / wf_file)
    scenario: Any = load(ROOT / "phenix-configs" / f"scenario-{variant}.yml")
    app: Any = load(FIXTURES / f"app-{variant}.yml")
    name: str = topology["metadata"]["name"]

    apps: list[Any] = [
        a for a in scenario["spec"]["apps"] if a["name"] != "cherrymountain"
    ]
    names: list[str] = [a["name"] for a in apps]
    # Before helics; right after ot-sim while a scenario has no helics block yet.
    at: int = names.index("helics") if "helics" in names else names.index("ot-sim") + 1
    apps.insert(at, app)
    return {
        "apiVersion": "phenix.sandia.gov/v1",
        "kind": "Experiment",
        "metadata": {
            "name": name,
            "annotations": {
                "topology": topology["metadata"]["name"],
                "scenario": scenario["metadata"]["name"],
            },
        },
        "spec": {
            "experimentName": name,
            "baseDir": f"/phenix/experiments/{name}",
            "defaultBridge": workflow["spec"]["defaultBridge"],
            "topology": {"nodes": topology_nodes(name)},
            "scenario": {"apps": apps},
            "vlans": {"aliases": workflow["spec"]["vlans"], "min": 100, "max": 199},
            "schedules": {},
            "deployMode": "no-headnode",
            "useGREMesh": False,
        },
        "status": {"startTime": "", "schedules": {}, "apps": {}, "vlans": {}},
    }


def main() -> int:
    FIXTURES.mkdir(exist_ok=True)
    for variant in VARIANTS:
        out: Path = FIXTURES / f"experiment-{variant}.json"
        out.write_text(
            json.dumps(experiment(variant), indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
