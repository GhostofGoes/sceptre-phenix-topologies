"""The recorded experiments are the ones the committed configs give.

Every stage test runs on ``fixtures/experiment-<variant>.json``. A fixture
recorded before a topology, scenario, or workflow change tests an experiment
that phenix would no longer build, so this fails until it is re-recorded.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from cherrymountain_apps.cherrymountain.tests import build_fixtures


@pytest.mark.parametrize("variant", sorted(build_fixtures.VARIANTS))
def test_the_app_block_fixture_is_the_scenarios_block(variant: str) -> None:
    """fixtures/app-<variant>.yml is a hand-kept copy of the block plantgen
    emits into the scenario: the same keys (``runPeriodically`` too), the same
    metadata, and the same hosts, in any order."""
    scenario: Any = build_fixtures.load(
        build_fixtures.ROOT / "phenix-configs" / f"scenario-{variant}.yml"
    )
    block: dict[str, Any] = next(
        a for a in scenario["spec"]["apps"] if a["name"] == "cherrymountain"
    )
    fixture: dict[str, Any] = build_fixtures.load(
        build_fixtures.FIXTURES / f"app-{variant}.yml"
    )

    def by_host(app: dict[str, Any]) -> dict[str, Any]:
        return {
            **app,
            "hosts": {h["hostname"]: h.get("metadata") for h in app["hosts"]},
        }

    assert by_host(fixture) == by_host(block), (
        f"fixtures/app-{variant}.yml differs from the cherrymountain block of "
        f"scenario-{variant}.yml"
    )
    assert block["runPeriodically"] == "5m"


@pytest.mark.parametrize("variant", sorted(build_fixtures.VARIANTS))
def test_the_recorded_experiment_is_current(variant: str) -> None:
    recorded: Any = json.loads(
        (build_fixtures.FIXTURES / f"experiment-{variant}.json").read_text(encoding="utf-8")
    )
    assert recorded == build_fixtures.experiment(variant), (
        f"fixtures/experiment-{variant}.json is stale: run "
        "python3 -I cherrymountain_apps/cherrymountain/tests/build_fixtures.py in apps/"
    )
