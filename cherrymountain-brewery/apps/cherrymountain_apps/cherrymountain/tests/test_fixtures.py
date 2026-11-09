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
def test_the_recorded_experiment_is_current(variant: str) -> None:
    recorded: Any = json.loads(
        (build_fixtures.FIXTURES / f"experiment-{variant}.json").read_text(encoding="utf-8")
    )
    assert recorded == build_fixtures.experiment(variant), (
        f"fixtures/experiment-{variant}.json is stale: run "
        "python3 -I cherrymountain_apps/cherrymountain/tests/build_fixtures.py in apps/"
    )
