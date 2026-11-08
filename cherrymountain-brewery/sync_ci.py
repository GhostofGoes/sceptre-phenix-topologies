#!/usr/bin/env python3
"""Keep the ci variant's copies of generated files in step with their sources.

phenix injects only what is under ``phenix-injects/`` (``phenix workflow apply``
copies that directory to the server), so files that plantgen and the FUXA
builder write elsewhere have to be copied there. This script owns those copies
and checks that ``phenix-configs/scenario-ci.yml`` embeds the generated ot-sim
and ntp blocks verbatim.

Usage:
    python sync_ci.py            # copy the files
    python sync_ci.py --check    # fail if a copy or an embedded block drifted
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

ROOT: Path = Path(__file__).resolve().parent
GENERATED: Path = ROOT / "plant" / "generated" / "ci"
INJECTS: Path = ROOT / "phenix-injects"
SCENARIO: Path = ROOT / "phenix-configs" / "scenario-ci.yml"


class Copy(BaseModel):
    """One file copied from its source of truth into phenix-injects/."""

    src: Path
    dst: Path


COPIES: list[Copy] = [
    Copy(src=GENERATED / "helics.json", dst=INJECTS / "sim" / "helics.json"),
    Copy(
        src=GENERATED / "openplc" / "plc-cellar" / "modbus_master.json",
        dst=INJECTS / "openplc" / "plc-cellar" / "conf" / "modbus_master.json",
    ),
    Copy(
        src=ROOT / "fuxa" / "scada-fuxa-project.json",
        dst=INJECTS / "fuxa" / "scada-fuxa-project.json",
    ),
]

EMBEDDED: dict[str, Path] = {
    "ot-sim": GENERATED / "phenix" / "ot-sim.yaml",
    "ntp": GENERATED / "phenix" / "ntp.yaml",
}


def check_embedded() -> list[str]:
    """Compare the scenario's app blocks with the generated YAML."""
    scenario: dict[str, Any] = yaml.safe_load(SCENARIO.read_text())
    apps: dict[str, Any] = {a["name"]: a for a in scenario["spec"]["apps"]}
    problems: list[str] = []
    for name, path in EMBEDDED.items():
        want: Any = yaml.safe_load(path.read_text())
        if apps.get(name) != want:
            problems.append(f"{SCENARIO.name}: app {name} differs from {path.relative_to(ROOT)}")
    order: list[str] = [a["name"] for a in scenario["spec"]["apps"]]
    if order.index("ot-sim") > order.index("helics"):
        problems.append("ot-sim must come before helics in the scenario")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    args: argparse.Namespace = ap.parse_args(argv)

    problems: list[str] = []
    for c in COPIES:
        if not c.src.exists():
            problems.append(f"missing source {c.src.relative_to(ROOT)}")
            continue
        same: bool = c.dst.exists() and c.dst.read_bytes() == c.src.read_bytes()
        if same:
            continue
        if args.check:
            problems.append(f"{c.dst.relative_to(ROOT)} differs from {c.src.relative_to(ROOT)}")
        else:
            c.dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(c.src, c.dst)
            print(f"copied {c.src.relative_to(ROOT)} -> {c.dst.relative_to(ROOT)}")
    problems += check_embedded()

    for p in problems:
        print(f"FAIL: {p}", file=sys.stderr)
    if not problems:
        print("ci copies and embedded blocks are in sync")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
