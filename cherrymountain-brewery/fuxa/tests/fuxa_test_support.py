"""Shared helpers of the fuxa tests: the project builder, Node, and test projects."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

FUXA_DIR: Path = Path(__file__).resolve().parents[1]
ROOT: Path = FUXA_DIR.parent
NODE: str | None = shutil.which("node")
NODE_MESSAGE: str = (
    "node not found: the batch engine tests run the generated FUXA script under "
    "Node 18 or newer (CI runners have it; the cm-fuxa-batch-tests hook needs it)"
)


def require_node() -> str:
    """Node's path; skip the test when it is missing, but fail a CI run: a
    silent skip there would drop the engine's whole test suite."""
    if NODE is None:
        if os.environ.get("CI"):
            pytest.fail(NODE_MESSAGE)
        pytest.skip(NODE_MESSAGE)
    return NODE


def load_builder() -> ModuleType:
    """fuxa/build_project.py, loaded by path as the cherrymountain app loads it."""
    spec = importlib.util.spec_from_file_location("cm_fuxa_build_project", FUXA_DIR / "build_project.py")
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # pydantic resolves postponed annotations through it
    spec.loader.exec_module(module)
    return module


def build_project(variant: str, host: str = "scada-fuxa") -> dict[str, Any]:
    """The project of a host as plain JSON (what is POSTed to FUXA)."""
    builder: ModuleType = load_builder()
    project: dict[str, Any] = json.loads(builder.render(builder.build_project(variant, host)))
    return project
