"""Shared fixtures: the real repository and a mutable copy of it."""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT: Path = Path(__file__).resolve().parents[3]
# sync.py and the BBMD service live outside the package.
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "phenix-injects" / "bbmd"))


@pytest.fixture(scope="session")
def repo() -> Path:
    return ROOT


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A copy of the configs and injects that a test can edit."""
    dst: Path = tmp_path / "cm"
    shutil.copytree(ROOT / "phenix-configs", dst / "phenix-configs")
    shutil.copytree(ROOT / "phenix-injects", dst / "phenix-injects")
    for wf in ROOT.glob("phenix*.yml"):
        shutil.copy(wf, dst / wf.name)
    return dst


Edit = Callable[[dict[str, Any]], None]


def edit_yaml(path: Path, edit: Edit) -> None:
    """Load a YAML file, apply ``edit`` to the document, write it back."""
    doc: dict[str, Any] = yaml.safe_load(path.read_text())
    edit(doc)
    path.write_text(yaml.safe_dump(doc, sort_keys=False, width=1000))


def node_of(doc: dict[str, Any], hostname: str) -> dict[str, Any]:
    for n in doc["spec"]["nodes"]:
        if n["general"]["hostname"] == hostname:
            return n
    raise KeyError(hostname)


def vrouter_host(doc: dict[str, Any], hostname: str) -> dict[str, Any]:
    app: dict[str, Any] = next(a for a in doc["spec"]["apps"] if a["name"] == "vrouter")
    return next(h for h in app["hosts"] if h["hostname"] == hostname)["metadata"]


def ruleset(doc: dict[str, Any], hostname: str, name: str) -> dict[str, Any]:
    return next(r for r in vrouter_host(doc, hostname)["acl"]["rulesets"] if r["name"] == name)
