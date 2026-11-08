#!/usr/bin/env python3
"""Stand-in BACnet/IP responder for the spike: device 3003 with one CO2 AI.

Usage: responder.py --name bas-vent --instance 3003 --address 10.20.60.13/24:47808
Install: pip install bacpypes3
Derived from bacpypes3 samples/mini-device-revisited.py (device object comes
from the standard `SimpleArgumentParser` arguments). The CO2 value ramps
between 400 and 1200 ppm so a client can see it change.
"""
import asyncio
import itertools

from bacpypes3.argparse import SimpleArgumentParser
from bacpypes3.app import Application
from bacpypes3.local.analog import AnalogInputObject

INTERVAL_S: float = 2.0
RAMP_PPM: list[float] = [float(v) for v in range(400, 1201, 100)]


async def main() -> None:
    args = SimpleArgumentParser().parse_args()
    app = Application.from_args(args)
    co2 = AnalogInputObject(
        objectIdentifier=("analogInput", 0),
        objectName="co2",
        presentValue=RAMP_PPM[0],
        statusFlags=[0, 0, 0, 0],
        units="partsPerMillion",
        description="Zone CO2",
    )
    app.add_object(co2)
    for ppm in itertools.cycle(RAMP_PPM):
        co2.presentValue = ppm
        await asyncio.sleep(INTERVAL_S)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
