"""Command line: ``python -m cherrymountain_viz`` (the cm-viz service).

In the range (sim-ctl, the ``cm-viz`` unit)::

    python -m cherrymountain_viz --source simctl \\
        --helics /etc/cherrymountain/helics.json --port 8090

Offline, with every federate in-process (needs ``cherrymountain-sim[power]``)::

    python -m cherrymountain_viz --source local --scenario co2-attack --speed 5
    # then open http://127.0.0.1:8090/ (3D) or http://127.0.0.1:8090/svg.html

The server binds ``127.0.0.1`` and is reached only through the autotunnel
(DESIGN.md §15.10): the view is ground truth, so it never listens on a range
network.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
from collections.abc import Sequence
from pathlib import Path

from cherrymountain_viz.scenarios import SCENARIOS
from cherrymountain_viz.server import run
from cherrymountain_viz.sources import LocalSource, SimctlSource, Source, load_buses

LOG: logging.Logger = logging.getLogger("cherrymountain_viz")
DEFAULT_HELICS: Path = Path("/etc/cherrymountain/helics.json")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the cm-viz server."""
    ap = argparse.ArgumentParser(prog="cm-viz", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--source", choices=("simctl", "local"), default="simctl")
    ap.add_argument(
        "--simctl-url", default="http://127.0.0.1:8080", help="sim-ctl event API (source simctl)"
    )
    ap.add_argument(
        "--helics",
        type=Path,
        default=DEFAULT_HELICS,
        help="plantgen helics.json (load buses; the federation for source local)",
    )
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="none")
    ap.add_argument("--speed", type=float, default=1.0, help="simulated s per wall s (local)")
    ap.add_argument(
        "--fast-forward", type=float, default=0.0, help="simulated s to run first (local)"
    )
    ap.add_argument(
        "--scenario-at", type=float, default=0.0, help="shift the scenario's steps by S (local)"
    )
    ap.add_argument("--rate", type=float, default=2.0, help="most frames per second sent")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("websockets").setLevel(logging.WARNING)
    if args.host not in ("127.0.0.1", "::1", "localhost"):
        LOG.warning("binding %s: the view is ground truth; keep it off range networks", args.host)
    source: Source
    if args.source == "local":
        local = LocalSource(
            args.helics,
            speed=args.speed,
            scenario=args.scenario,
            fast_forward_s=args.fast_forward,
            scenario_at_s=args.scenario_at,
        )
        local.start()
        source = local
    else:
        poller = SimctlSource(args.simctl_url, buses=load_buses(args.helics))
        poller.start()
        source = poller
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(source, args.host, args.port, args.rate))
    return 0
