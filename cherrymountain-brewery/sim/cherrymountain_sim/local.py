"""Offline co-simulation: every federate of a variant in one process, no HELICS.

Routes publications to subscriptions and endpoint messages to endpoints
exactly as plantgen's helics.json lists them, with the one-step latency of the
real federation (DESIGN.md §9.2): a value published in step k is read in step
k + 1. Used by the tests (whole-plant smoke test, event routing) and for quick
demos::

    python -m cherrymountain_sim.local \
        --config plant/generated/full/helics.json --hours 2
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from pathlib import Path

from cherrymountain_sim.federate import Federate, HelicsConfig, Value, build
from cherrymountain_sim.plcloop import PlcLink


class LocalFederation:
    """All federates of a helics.json, stepped together at 1 s (or ``dt``).

    ``plcs`` closes the loop for PLCs (:class:`~cherrymountain_sim.plcloop.PlcLink`):
    each step, once the federation has published, every PLC scans on the values
    of the previous step and writes its outputs to the federates before they
    step, the same one-step latency as a field device.
    """

    config: HelicsConfig
    feds: dict[str, Federate]
    values: dict[str, Value]
    plcs: list[PlcLink]
    t: float

    def __init__(
        self,
        config: HelicsConfig,
        names: Sequence[str] | None = None,
        plcs: Sequence[PlcLink] | None = None,
    ) -> None:
        self.config = config
        self.feds = {n: build(config, n) for n in (names or list(config.federates))}
        self.values = {}
        self.plcs = list(plcs or [])
        self.t = 0.0

    def step(self, dt: float = 1.0) -> None:
        """Advance every federate whose period is due by ``dt`` seconds."""
        if self.values:
            for link in self.plcs:
                link.scan(self.values, self.feds, self.t)
        for name, fed in self.feds.items():
            period = self.config.federates[name].period_s
            if self.t % period > 1e-9 and period > dt:
                continue
            for sub in self.config.federates[name].subscriptions:
                if sub.key in self.values:
                    fed.apply_input(sub.key, self.values[sub.key])
            fed.step(max(period, dt))
        for fed in self.feds.values():
            for dest, payload in fed.outbox:
                target = next(
                    (f for f in self.feds.values() if f.cfg.endpoint == dest), None
                )
                if target is not None:
                    target.handle_message(payload)
            fed.outbox.clear()
            self.values.update(fed.values())
        self.t += dt

    def run(self, seconds: float) -> None:
        """Run ``seconds`` of federation time."""
        for _ in range(round(seconds)):
            self.step(1.0)


def main(argv: Sequence[str] | None = None) -> int:
    """Run a federation offline and print a summary of every federate's truth."""
    ap = argparse.ArgumentParser(prog="cherrymountain-local", description=__doc__)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--hours", type=float, default=1.0)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    fed = LocalFederation(HelicsConfig.load(args.config))
    fed.run(args.hours * 3600)
    summary = {
        "sim_time": next(iter(fed.feds.values())).now.isoformat(),
        **{
            k: v
            for k, v in sorted(fed.values.items())
            if "/load." in k or k.startswith("power/")
        },
    }
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
