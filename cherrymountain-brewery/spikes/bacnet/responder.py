#!/usr/bin/env python3
"""Stand-in BACnet/IP responder for the spike: device 3003 with one CO2 AI.

Usage: responder.py --name bas-vent --instance 3003 --address 10.20.60.13/24:47808
       [--broadcast-iam] [--announce SECONDS]
With --broadcast-iam the device answers a Who-Is with a broadcast I-Am (as many
real devices do) instead of BACpypes3's default unicast reply to the requester.
--announce also sends an unsolicited broadcast I-Am every SECONDS (as a device
does when it powers up).
Install: pip install bacpypes3
Derived from bacpypes3 samples/mini-device-revisited.py (device object comes
from the standard `SimpleArgumentParser` arguments). The CO2 value ramps
between 400 and 1200 ppm so a client can see it change.
"""
import asyncio
import itertools

from bacpypes3.app import Application
from bacpypes3.argparse import SimpleArgumentParser
from bacpypes3.local.analog import AnalogInputObject

INTERVAL_S: float = 2.0
RAMP_PPM: list[float] = [float(v) for v in range(400, 1201, 100)]


class BroadcastIAmApplication(Application):
    """Answers every Who-Is with a broadcast I-Am (the base class unicasts it to the requester)."""

    def i_am(self, address=None) -> None:
        super().i_am(address=None)


async def main() -> None:
    parser = SimpleArgumentParser()
    parser.add_argument("--broadcast-iam", action="store_true", help="answer Who-Is with a broadcast I-Am")
    parser.add_argument("--announce", type=float, default=0.0, help="send an unsolicited broadcast I-Am every N seconds")
    args = parser.parse_args()
    app = (BroadcastIAmApplication if args.broadcast_iam else Application).from_args(args)
    co2 = AnalogInputObject(
        objectIdentifier=("analogInput", 0),
        objectName="co2",
        presentValue=RAMP_PPM[0],
        statusFlags=[0, 0, 0, 0],
        units="partsPerMillion",
        description="Zone CO2",
    )
    app.add_object(co2)
    since_announce: float = 0.0
    for ppm in itertools.cycle(RAMP_PPM):
        co2.presentValue = ppm
        await asyncio.sleep(INTERVAL_S)
        since_announce += INTERVAL_S
        if args.announce and since_announce >= args.announce:
            since_announce = 0.0
            app.i_am()  # no address: a broadcast


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
