"""Shared by the brand tests of the screens: the repository's paths, its topology nodes, and a
way to run an injected script on stand-ins."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import yaml

ROOT: Path = Path(__file__).resolve().parents[2]
INJECTS: Path = ROOT / "phenix-injects"
CONFIGS: Path = ROOT / "phenix-configs"
PREFIX: str = "/phenix/injects/${BRANCH_NAME}/"


def topology_nodes() -> dict[str, dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}
    for path in sorted(CONFIGS.glob("topology-*.yml")):
        for n in yaml.safe_load(path.read_text())["spec"].get("nodes") or []:
            nodes[n["general"]["hostname"]] = n
    return nodes


def run(script: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), *args],
        env={"PATH": os.environ["PATH"], "HOME": "/nonexistent", **env},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
