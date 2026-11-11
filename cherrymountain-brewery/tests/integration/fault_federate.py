#!/usr/bin/env python3
"""The cellar federate with FV-101 head pressure forced above its alarm limit.

Used by the integration test's fault phase: it is ``cherrymountain_sim``'s own
model and HELICS wiring (same federate name, keys, and period), but the
``cellar/fv101.pressure_psia`` publication is replaced by a fixed value so
the PLC's head pressure high alarm (30 s on-delay) can be observed quickly.

Usage:
    python fault_federate.py --config helics.json --broker-address 127.0.0.1 \\
        [--pressure-psia 27.0]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

LOG: logging.Logger = logging.getLogger("fault_federate")
PRESSURE_KEY: str = "cellar/fv101.pressure_psia"


def run(config: Path, broker_address: str, pressure_psia: float) -> None:
    """Run the cellar federate in real time with one publication overridden."""
    import helics as h  # noqa: PLC0415
    from cherrymountain_sim.federate import CellarFederate, HelicsConfig  # noqa: PLC0415

    hcfg: HelicsConfig = HelicsConfig.load(config)
    fcfg = hcfg.federates["cellar"]
    logic: CellarFederate = CellarFederate(fcfg)

    fi = h.helicsCreateFederateInfo()
    h.helicsFederateInfoSetCoreTypeFromString(fi, hcfg.broker.core_type)
    h.helicsFederateInfoSetCoreInitString(fi, f"--federates=1 --broker_address={broker_address}")
    h.helicsFederateInfoSetTimeProperty(fi, h.HELICS_PROPERTY_TIME_PERIOD, fcfg.period_s)
    h.helicsFederateInfoSetFlagOption(fi, h.HELICS_FLAG_REALTIME, True)
    h.helicsFederateInfoSetTimeProperty(fi, h.HELICS_PROPERTY_TIME_RT_LAG, 1.0)
    h.helicsFederateInfoSetTimeProperty(fi, h.HELICS_PROPERTY_TIME_RT_LEAD, 1.0)
    fed = h.helicsCreateCombinationFederate("cellar", fi)
    pubs = {
        p.key: (h.helicsFederateRegisterGlobalTypePublication(fed, p.key, p.type, p.units or ""), p)
        for p in fcfg.publications
    }
    ep = h.helicsFederateRegisterGlobalEndpoint(fed, fcfg.endpoint, "")
    h.helicsFederateEnterExecutingMode(fed)
    LOG.info("fault federate executing, %s forced to %.2f psia", PRESSURE_KEY, pressure_psia)
    t: float = 0.0
    try:
        while t < hcfg.end_time_s:
            t = h.helicsFederateRequestTime(fed, t + fcfg.period_s)
            while h.helicsEndpointHasMessage(ep):
                logic.handle_message(h.helicsMessageGetString(h.helicsEndpointGetMessage(ep)))
            logic.step(fcfg.period_s)
            for key, (pub, spec) in pubs.items():
                if key == PRESSURE_KEY:
                    h.helicsPublicationPublishDouble(pub, pressure_psia)
                elif spec.type == "boolean":
                    h.helicsPublicationPublishBoolean(pub, bool(logic.value(spec)))
                else:
                    h.helicsPublicationPublishDouble(pub, float(logic.value(spec)))
    finally:
        h.helicsFederateDisconnect(fed)
        h.helicsFederateFree(fed)
        h.helicsCloseLibrary()


def main(argv: list[str] | None = None) -> int:
    ap: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--broker-address", required=True)
    ap.add_argument("--pressure-psia", type=float, default=27.0)
    args: argparse.Namespace = ap.parse_args(argv)
    logging.basicConfig(level="INFO", format="%(asctime)s %(name)s %(message)s")
    run(args.config, args.broker_address, args.pressure_psia)
    return 0


if __name__ == "__main__":
    sys.exit(main())
