"""Run the app on the recorded ci experiment (fixtures/experiment-ci.json)."""

from __future__ import annotations

import copy
import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from cherrymountain_apps.cherrymountain.app import CherryMountain

HERE: Path = Path(__file__).resolve().parent
#: The cherrymountain-brewery directory: the app reads plant.yaml, the
#: plantgen output, and phenix-injects/ from it, as sourceDir in a range.
ROOT: Path = HERE.parents[3]
EXPERIMENT: dict[str, Any] = json.loads(
    (HERE / "fixtures" / "experiment-ci.json").read_text()
)


def app_block(exp: dict[str, Any]) -> dict[str, Any]:
    return next(
        a for a in exp["spec"]["scenario"]["apps"] if a["name"] == "cherrymountain"
    )


def node(exp: dict[str, Any], hostname: str) -> dict[str, Any]:
    return next(
        n
        for n in exp["spec"]["topology"]["nodes"]
        if n["general"]["hostname"] == hostname
    )


@pytest.fixture
def experiment(tmp_path: Path) -> dict[str, Any]:
    """A fresh copy of the ci experiment, rooted at tmp_path."""
    exp: dict[str, Any] = copy.deepcopy(EXPERIMENT)
    exp["spec"]["baseDir"] = str(tmp_path / "exp")
    app_block(exp)["metadata"]["sourceDir"] = str(ROOT)
    return exp


RunApp = Callable[..., CherryMountain]


@pytest.fixture
def run_app(monkeypatch: pytest.MonkeyPatch, experiment: dict[str, Any]) -> RunApp:
    """Build the app from the experiment on stdin and run one stage."""

    def run(
        stage: str, exp: dict[str, Any] | None = None, dryrun: bool = False
    ) -> CherryMountain:
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(exp or experiment)))
        app: CherryMountain = CherryMountain("cherrymountain", stage, dryrun)
        {
            "configure": app.configure,
            "pre-start": app.pre_start,
            "post-start": app.post_start,
            "running": app.running,
            "cleanup": app.cleanup,
        }[stage]()
        return app

    return run
