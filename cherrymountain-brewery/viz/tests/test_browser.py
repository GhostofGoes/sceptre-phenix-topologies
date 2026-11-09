"""Headless Chromium smoke test of the page (marked ``browser``; not in prek).

Runs the bridge on the offline harness and ``browser_smoke.cjs`` against it:
each scene must get a WebGL context, render frames, and bind the data. Needs
Node and the ``playwright`` package with its Chromium (``require('playwright')``
must work). Run it with::

    cd cherrymountain-brewery/viz && python -m pytest -m browser
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from cherrymountain_viz.server import BackgroundServer
from cherrymountain_viz.sources import LocalSource

HERE: Path = Path(__file__).resolve().parent
HELICS_FULL: Path = HERE.parents[1] / "plant" / "generated" / "full" / "helics.json"

pytestmark = pytest.mark.browser


def _playwright_ok() -> bool:
    node = shutil.which("node")
    if node is None:
        return False
    probe = subprocess.run(  # noqa: S603 - fixed argv
        [node, "-e", "require('playwright')"], capture_output=True, check=False, cwd=HERE
    )
    return probe.returncode == 0


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    """The bridge on the offline harness (co2-attack, so every panel has data),
    served from a background thread."""
    src = LocalSource(HELICS_FULL, scenario="co2-attack", speed=5.0, fast_forward_s=200.0)
    src.start()
    server = BackgroundServer(src)
    yield server.start()
    server.stop()
    src.stop.set()


@pytest.mark.skipif(not _playwright_ok(), reason="node with the playwright package is needed")
def test_every_scene_renders_with_data(base_url: str) -> None:
    node = shutil.which("node") or "node"
    run = subprocess.run(  # noqa: S603 - fixed argv
        [node, str(HERE / "browser_smoke.cjs"), base_url],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        cwd=HERE,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.count('"webgl":2') + run.stdout.count('"webgl":1') >= 4
