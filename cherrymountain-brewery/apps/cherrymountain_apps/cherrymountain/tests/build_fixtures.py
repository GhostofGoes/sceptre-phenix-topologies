#!/usr/bin/env python3
"""Record the experiment JSON fixtures from the ci topology and scenario.

phenix hands an app the whole experiment as JSON on stdin. This builds that
document the way phenix does for ``cherrymountain-ci``: the topology and
scenario specs from phenix-configs/, with fixtures/app-ci.yml inserted between
the ot-sim and helics apps, and the workflow's VLAN map. Run it after a change
to those files and commit the result:

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
EXPERIMENT: str = "cherrymountain-ci"


def load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def experiment() -> dict[str, Any]:
    topology: Any = load(ROOT / "phenix-configs" / "topology-ci.yml")
    scenario: Any = load(ROOT / "phenix-configs" / "scenario-ci.yml")
    workflow: Any = load(ROOT / "phenix-ci.yml")
    app: Any = load(FIXTURES / "app-ci.yml")

    apps: list[Any] = [
        a for a in scenario["spec"]["apps"] if a["name"] != "cherrymountain"
    ]
    at: int = next(i for i, a in enumerate(apps) if a["name"] == "helics")
    apps.insert(at, app)
    return {
        "apiVersion": "phenix.sandia.gov/v1",
        "kind": "Experiment",
        "metadata": {
            "name": EXPERIMENT,
            "annotations": {
                "topology": topology["metadata"]["name"],
                "scenario": scenario["metadata"]["name"],
            },
        },
        "spec": {
            "experimentName": EXPERIMENT,
            "baseDir": f"/phenix/experiments/{EXPERIMENT}",
            "defaultBridge": workflow["spec"]["defaultBridge"],
            "topology": topology["spec"],
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
    out: Path = FIXTURES / "experiment-ci.json"
    out.write_text(json.dumps(experiment(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
