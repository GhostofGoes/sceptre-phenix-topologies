"""Capture the cm-viz screenshots of the Examples section.

Each shot runs the real federates in-process (``LocalSource``, no HELICS),
plays one of the viz scenarios, serves the real page with the viz server, and
has headless Chromium (viz/tools/shoot.cjs, Playwright) wait for a condition on
the frame before it takes the picture. Nothing is drawn by this script.

    python viz_shots.py OUTDIR [--only NAME ...]

Needs the repo's viz and sim packages importable (pydantic, websockets,
opendssdirect), and Node with the ``playwright`` package. The repository root
is taken from CM_ROOT (the ``cherrymountain-brewery`` directory), else from
this file's place in the tree.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

ROOT: Path = Path(os.environ.get("CM_ROOT") or Path(__file__).resolve().parents[3])
VIZ: Path = ROOT / "viz"
sys.path[:0] = [str(VIZ), str(ROOT / "sim")]

from cherrymountain_viz.frame import HOLD_S  # noqa: E402
from cherrymountain_viz.server import BackgroundServer  # noqa: E402
from cherrymountain_viz.sources import LocalSource, Source, StaticSource  # noqa: E402

HELICS_FULL: Path = ROOT / "plant" / "generated" / "full" / "helics.json"
#: 04:30 start + 6.5 h: late morning, with sun on the PV array.
MIDDAY_S: float = 6.5 * 3600
#: The fan findings appear once held for HOLD_S after the source starts.
FANS_HELD: str = "f.findings.some((x) => x.text.includes('commanded off'))"
WIDTH, HEIGHT = 1600, 900


class Shot(BaseModel):
    """One screenshot: a scenario, how far to run it, and what to wait for."""

    name: str
    scene: str
    page: str = ""  # "" is the 3D page, "svg.html" the 2.5D one
    scenario: str = "none"
    fast_forward_s: float = 300.0
    scenario_at_s: float = 0.0
    view: str = "truth"
    wait: str = "true"  # JavaScript over `f`, the latest frame
    settle_ms: int = 3000
    #: Serve the scenario's frame frozen and marked stale (the NO DATA view).
    stale: bool = False
    width: int = WIDTH
    height: int = HEIGHT


SHOTS: tuple[Shot, ...] = (
    Shot(name="viz-cellar-normal", scene="cellar", fast_forward_s=900.0),
    Shot(
        name="viz-cellar-co2-leak",
        scene="cellar",
        scenario="co2-leak",
        fast_forward_s=1500.0,
        wait="f.cellar.air.tiers[0]",
    ),
    Shot(
        name="viz-cellar-co2-attack",
        scene="cellar",
        scenario="co2-attack",
        fast_forward_s=1500.0,
        wait=f"f.operator && f.operator.frozen && f.cellar.air.tiers[1] && {FANS_HELD}",
    ),
    Shot(
        name="viz-cellar-co2-attack-operator-view",
        scene="cellar",
        scenario="co2-attack",
        fast_forward_s=1500.0,
        view="operator",
        wait=f"f.operator && f.operator.frozen && {FANS_HELD}",
    ),
    Shot(
        name="viz-fermenters-psv-lift",
        scene="fermenters",
        scenario="spunding-attack",
        fast_forward_s=3430.0,
        wait="f.cellar.vessels.some((v) => v.id === 'FV102' && v.psv_lifted)",
        settle_ms=1800,
    ),
    Shot(name="viz-power-normal", scene="power", fast_forward_s=MIDDAY_S),
    Shot(
        name="viz-power-outage",
        scene="power",
        scenario="outage",
        scenario_at_s=MIDDAY_S - 120.0,
        fast_forward_s=MIDDAY_S,
        wait="f.power.ats_on_emergency && f.power.gen_running",
    ),
)


def take(shot: Shot, out: Path) -> dict[str, object]:
    """Run one shot; the JSON line shoot.cjs printed."""
    src = LocalSource(
        HELICS_FULL,
        scenario=shot.scenario,
        fast_forward_s=shot.fast_forward_s,
        scenario_at_s=shot.scenario_at_s,
    )
    served: Source = src
    if shot.stale:
        # Two frames HOLD_S apart, so the held findings are in the frozen one.
        src.advance_to(src.fed.t + HOLD_S + 1)
        src.publish()
        first = src.frame()
        assert first is not None
        served = StaticSource(first.model_copy(update={"stale": True}))
    else:
        src.start()
    server = BackgroundServer(served)
    base = server.start()
    try:
        page = shot.page or ""
        args = {
            "url": f"{base}/{page}?scene={shot.scene}&view={shot.view}",
            "out": str(out / f"{shot.name}.png"),
            "width": shot.width,
            "height": shot.height,
            "scale": 1,
            "wait": shot.wait,
            "settle_ms": shot.settle_ms,
            "timeout_ms": 120000,
        }
        run = subprocess.run(  # noqa: S603 - fixed argv
            [shutil.which("node") or "node", str(VIZ / "tools" / "shoot.cjs"), json.dumps(args)],
            capture_output=True,
            text=True,
            timeout=240,
            check=True,
            cwd=VIZ / "tools",
        )
        result: dict[str, object] = json.loads(run.stdout.strip().splitlines()[-1])
        return result
    finally:
        server.stop()
        src.stop.set()


def main(argv: Sequence[str] | None = None) -> int:
    """Take every shot (or the ``--only`` ones) into OUTDIR."""
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    for shot in SHOTS:
        if args.only and shot.name not in args.only:
            continue
        print(json.dumps(take(shot, args.outdir)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
