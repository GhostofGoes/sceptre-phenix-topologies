"""Render the documented cm-viz screenshots with the offline harness.

Each shot runs the real federates in-process (``LocalSource``), plays a
scenario, serves the page, and has headless Chromium (``shoot.cjs``, Playwright,
deviceScaleFactor 2) wait for a condition on the frame before taking the
picture::

    python tools/screenshots.py OUTDIR [--only NAME ...]

Needs ``cherrymountain-sim[power]``, ``websockets``, and Node with the
``playwright`` package. The PNGs are large; keep them out of the repository.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

HERE: Path = Path(__file__).resolve().parent
VIZ: Path = HERE.parent
sys.path[:0] = [str(VIZ), str(VIZ.parent / "sim")]

from cherrymountain_viz.server import BackgroundServer  # noqa: E402
from cherrymountain_viz.sources import LocalSource  # noqa: E402

HELICS_FULL: Path = VIZ.parent / "plant" / "generated" / "full" / "helics.json"
#: 04:30 start + 6.5 h: late morning, with sun on the PV array.
MIDDAY_S: float = 6.5 * 3600


class Shot(BaseModel):
    """One screenshot: a scenario, how far to run it, and what to wait for."""

    name: str
    scene: str
    scenario: str = "none"
    fast_forward_s: float = 300.0
    scenario_at_s: float = 0.0
    speed: float = 1.0
    view: str = "truth"
    wait: str = "true"  # JavaScript over `f`, the latest frame
    settle_ms: int = 3000


SHOTS: tuple[Shot, ...] = (
    Shot(name="cellar-normal", scene="cellar", fast_forward_s=900.0),
    Shot(
        name="cellar-co2-attack",
        scene="cellar",
        scenario="co2-attack",
        fast_forward_s=1500.0,
        wait="f.operator && f.operator.frozen && f.cellar.air.tiers[1]",
    ),
    Shot(
        name="cellar-co2-attack-operator-view",
        scene="cellar",
        scenario="co2-attack",
        fast_forward_s=1500.0,
        view="operator",
        wait="f.operator && f.operator.frozen",
    ),
    Shot(
        name="fermenters-psv-lift",
        scene="fermenters",
        scenario="spunding-attack",
        fast_forward_s=3430.0,
        wait="f.cellar.vessels.some((v) => v.id === 'FV102' && v.psv_lifted)",
        settle_ms=1800,
    ),
    Shot(name="power-normal", scene="power", fast_forward_s=MIDDAY_S),
    Shot(
        name="power-outage",
        scene="power",
        scenario="outage",
        scenario_at_s=MIDDAY_S - 120.0,
        fast_forward_s=MIDDAY_S,
        wait="f.power.ats_on_emergency && f.power.gen_running",
    ),
)


def take(shot: Shot, out: Path) -> dict[str, object]:
    """Run one shot; the line shoot.cjs printed."""
    src = LocalSource(
        HELICS_FULL,
        scenario=shot.scenario,
        speed=shot.speed,
        fast_forward_s=shot.fast_forward_s,
        scenario_at_s=shot.scenario_at_s,
    )
    src.start()
    server = BackgroundServer(src)
    base = server.start()
    try:
        args = {
            "url": f"{base}/?scene={shot.scene}&view={shot.view}&dpr=2",
            "out": str(out / f"{shot.name}.png"),
            "scale": 2,
            "wait": shot.wait,
            "settle_ms": shot.settle_ms,
            "timeout_ms": 120000,
        }
        node = shutil.which("node") or "node"
        run = subprocess.run(  # noqa: S603 - fixed argv
            [node, str(HERE / "shoot.cjs"), json.dumps(args)],
            capture_output=True,
            text=True,
            timeout=180,
            check=True,
            cwd=HERE,
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
